"""Thin FastAPI wrapper around the M1 index and deterministic M2 services."""

from __future__ import annotations

import hashlib
import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from functools import wraps
from pathlib import Path
from typing import Annotated, Literal
from zipfile import BadZipFile, is_zipfile

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import BaseModel, Field
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.adapters import ingest_excel
from app.adapters.pdf_adapter import EXTENDED_TARGET_CODES, PdfDocumentSpec, PdfIngestionResult, extract_docling_tables
from app.adapters.supplemental_adapter import extract_note_candidates
from app.models import DocumentContext, Evidence, FactState, FinancialFact, LineItemCode, PDFLocator, Period, PeriodType, ReportScope
from app.models.financial_fact import VerificationRecord
from app.models.supplemental_fact import AnchorRelation, SupplementalFact
from app.services.supplemental_guard import check_anchor, check_receivable_group, check_receivable_subsets, check_cash_group, supplemental_id
from app.services.question_router import normalize_note_ref
from app.services.qa_service import DocumentIndex, QAResponse, answer_question
from app.models.health_review import HealthReportResponse
from app.services.health_review import review_health
from app.services.tender_review import TenderCriteriaConfig, TenderReviewResponse, review_tender
from app.models.full_analysis import FullAnalysisReport
from app.services.analysis_book import AnalysisBook
from app.services.full_analysis_report import build_full_report
from app.services.time_series_stitcher import stitch_indexes
from app.services.fact_identity import pdf_fact_id
from app.services.vas_normalizer import shift_year
from app.services.pdf_prescan import scan_pdf, region_box
from app.services.statement_reconciliation import reconcile_statement
from app.services.metric_matrix import metric_matrix


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PDF_UPLOAD_BYTES = 30 * 1024 * 1024
PRESCAN_VERSION = "phase1-v8"
DEFAULT_UPLOAD_DIR = Path(__file__).resolve().parents[2] / "data" / "uploads"


class IngestResponse(BaseModel):
    document_hash: str
    fact_count: int
    note_count: int
    sheet_count: int
    states: dict[str, int]


class PdfIngestResponse(BaseModel):
    document_hash: str
    fact_count: int
    states: dict[str, int]
    diagnostics: list[str]
    page_start: int
    page_end: int


class ConfirmedPdfPage(BaseModel):
    page_number: int = Field(ge=1, le=10000)
    form_code: Literal["B01-DN", "B02-DN", "B03-DN"]
    region: Literal["FULL", "LEFT", "RIGHT", "TOP", "BOTTOM"] = "FULL"


class ConfirmedPdfExtraction(BaseModel):
    prescan_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    company_name: str = Field(min_length=1, max_length=200)
    company_id: str = Field(min_length=1, max_length=80)
    report_scope: ReportScope
    fiscal_start: date
    fiscal_end: date
    unit_label: Literal["đồng", "nghìn đồng", "triệu đồng", "tỷ đồng"]
    reviewer: str = Field(min_length=1, max_length=200)
    reviewed_page_map: Literal[True]
    pages: list[ConfirmedPdfPage] = Field(min_length=1, max_length=300)
    ocr: bool = False
    extended_codes: bool = True


def run_pdf_converter(source: Path, output: Path, start: int, end: int, ocr: bool) -> dict:
    """Run optional Docling in a bounded separate process; never invoke a shell."""
    root = Path(__file__).resolve().parents[2]
    bundled = root / ".venv-m4b" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    executable = os.environ.get("M4B_DOCLING_PYTHON") or str(bundled if bundled.is_file() else sys.executable)
    script = root / "scripts" / "run_docling_extract.py"
    command = [executable, "-X", "utf8", str(script), str(source), str(output),
               "--start", str(start), "--end", str(end), "--timeout", "240"]
    if ocr:
        command.append("--ocr")
    try:
        completed = subprocess.run(command, capture_output=True, timeout=300, check=False)
        if completed.returncode or not output.exists():
            raise RuntimeError("Docling conversion failed; check the configured Python environment")
        payload = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise RuntimeError("Docling is unavailable or conversion timed out") from error
    if str(payload.get("status", "")).split(".")[-1] != "SUCCESS" or payload.get("errors"):
        raise RuntimeError("Docling did not complete the requested pages successfully")
    return payload


class QueryRequest(BaseModel):
    document_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    question: str = Field(min_length=1)


class HealthReviewRequest(BaseModel):
    document_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    year: int | None = Field(default=None, ge=1900, le=2100)


class FullAnalysisRequest(BaseModel):
    document_hashes: list[str] = Field(min_length=1, max_length=20)
    years: list[int] | None = None


class LiveAnalysisRequest(FullAnalysisRequest):
    restatement_policy: Literal["ORIGINAL_AS_REPORTED", "LATEST_COMPARATIVE"] = "ORIGINAL_AS_REPORTED"
    include_comparative_periods: bool = False


class P10SimulationRequest(LiveAnalysisRequest):
    fiscal_year: int = Field(ge=1900, le=2100)
    revenue_years: int = Field(default=3, ge=1, le=10)
    revenue_factor: Decimal = Field(default=Decimal('1.5'), gt=0, allow_inf_nan=False)
    duration_years: Decimal = Field(default=Decimal('1'), gt=0, allow_inf_nan=False)
    financial_resource_ratio: Decimal = Field(default=Decimal('0.3'), gt=0, le=1, allow_inf_nan=False)


class TenderReviewRequest(BaseModel):
    document_hashes: list[str] = Field(min_length=1, max_length=20)
    config: TenderCriteriaConfig

    @classmethod
    def validate_hashes(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values) or any(len(h) != 64 or any(c not in "0123456789abcdef" for c in h) for h in values):
            raise ValueError("document hashes must be unique lowercase SHA-256")
        return values


class PdfFactReviewRequest(BaseModel):
    fact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: str = Field(min_length=1, max_length=200, pattern=r".*\S.*")
    decision: Literal["CONFIRM", "REJECT"]
    reviewed_source_and_context: Literal[True]
    confirmed_normalized_value: Decimal = Field(allow_inf_nan=False)
    confirmed_currency: str = Field(min_length=1)
    confirmed_period: Period
    comment: str = Field(default="", max_length=1000)


class PdfCorrectionRequest(BaseModel):
    fact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: str = Field(min_length=1, max_length=200, pattern=r".*\S.*")
    reviewed_source_and_context: Literal[True]
    corrected_normalized_value: Decimal = Field(allow_inf_nan=False)
    confirmed_currency: str = Field(min_length=1)
    confirmed_period: Period
    comment: str = Field(min_length=1, max_length=1000)
    corrected_locator: PDFLocator | None = None
    corrected_observed_text: str | None = Field(default=None, min_length=1, max_length=500)


class PdfMissingFactRequest(BaseModel):
    form_code: Literal["B01-DN", "B02-DN", "B03-DN"]
    code: str = Field(pattern=r"^[0-9]{2,3}$")
    original_label: str = Field(min_length=1, max_length=200)
    period: Period
    normalized_value_vnd: Decimal = Field(allow_inf_nan=False)
    locator: PDFLocator
    observed_text: str = Field(min_length=1, max_length=500)
    reviewer: str = Field(min_length=1, max_length=200, pattern=r".*\S.*")
    reviewed_source_and_context: Literal[True]
    comment: str = Field(min_length=1, max_length=1000)


class PdfBatchFact(BaseModel):
    fact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed_normalized_value: Decimal = Field(allow_inf_nan=False)


class PdfBatchReviewRequest(BaseModel):
    form_code: Literal["B01-DN", "B02-DN", "B03-DN"]
    fiscal_year: int = Field(ge=1900, le=2100)
    reviewer: str = Field(min_length=1, max_length=200, pattern=r".*\S.*")
    reviewed_all_source_pages: Literal[True]
    rows: list[PdfBatchFact] = Field(min_length=1)


class PdfNoteReviewRequest(BaseModel):
    fact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: str = Field(min_length=1, max_length=200, pattern=r".*\S.*")
    note_ref: str = Field(min_length=3, max_length=20)
    page_number: int = Field(ge=1)
    excerpt: str = Field(min_length=1, max_length=3000, pattern=r".*\S.*")
    reviewed_source_and_link: Literal[True]


class SupplementalProposalRequest(BaseModel):
    concept: str
    fiscal_year: int = Field(ge=1900, le=2100)
    value_vnd: Decimal = Field(allow_inf_nan=False)
    parent_vas_code: str = Field(pattern=r"^B0[123]:(?:[0-9]{2,3})$")
    anchor_relation: AnchorRelation
    locator: PDFLocator
    observed_text: str = Field(min_length=1, max_length=3000)
    note_ref: str | None = None


class SupplementalSignoffRequest(BaseModel):
    fact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: str = Field(min_length=1, max_length=200)
    confirmed_value_vnd: Decimal = Field(allow_inf_nan=False)
    reviewed_source_and_context: Literal[True]
    decision: Literal["CONFIRM", "REJECT"]


class SupplementalExtractRequest(BaseModel):
    page_start: int = Field(ge=1, le=10000)
    page_end: int = Field(ge=1, le=10000)
    fiscal_year: int = Field(ge=1900, le=2100)
    ocr: bool = False


def create_app(*, upload_dir: Path = DEFAULT_UPLOAD_DIR, pdf_converter=run_pdf_converter) -> FastAPI:
    application = FastAPI(title="Financial Statement Evidence API", version="0.2.0")
    application.state.documents: dict[str, DocumentIndex] = {}
    application.state.upload_dir = upload_dir
    lock = threading.RLock()

    @application.get("/v1/analysis/metric-matrix")
    def analysis_metric_matrix():
        return {"version": "phase3-v1", "metrics": metric_matrix(),
                "note": "Formula field is the catalogue shorthand; each calculation returns its exact branch formula."}

    demo_registry = {
        "pnj-2021-2025": ("PNJ · 2021–2025", Path(__file__).resolve().parents[2] / "benchmarks/p5_pnj_manual_report.json", "Chép tay, chờ ký duyệt độc lập"),
        "vhc-2024": ("Vĩnh Hoàn · 2024", Path(__file__).resolve().parents[2] / "benchmarks/p6_vhc_real_extracted.json", "PDF thật, OCR chưa ký duyệt"),
    }

    @application.get("/analysis")
    def analysis_dashboard():
        return FileResponse(Path(__file__).resolve().parents[2] / "app/web/analysis.html",media_type="text/html")

    @application.get("/audit")
    def audit_dashboard():
        return FileResponse(Path(__file__).resolve().parents[2] / "app/web/audit.html",media_type="text/html")

    @application.get("/assets/metric-labels.js")
    def metric_labels():
        return FileResponse(Path(__file__).resolve().parents[2] / "app/web/metric_labels.js", media_type="text/javascript")

    @application.get("/assets/decimal-format.js")
    def decimal_format_js() -> FileResponse:
        return FileResponse(Path(__file__).resolve().parents[2] / "app/web/decimal_format.js", media_type="text/javascript")

    @application.get("/assets/workbench.js")
    def workbench_script():
        return FileResponse(Path(__file__).resolve().parents[2] / "app/web/workbench.js", media_type="text/javascript")

    @application.get("/assets/workbench.css")
    def workbench_style():
        return FileResponse(Path(__file__).resolve().parents[2] / "app/web/workbench.css", media_type="text/css")

    @application.get("/assets/ui-refresh.css")
    def ui_refresh_style():
        return FileResponse(Path(__file__).resolve().parents[2] / "app/web/ui-refresh.css", media_type="text/css")

    @application.get("/assets/p10.js")
    def p10_script():
        return FileResponse(Path(__file__).resolve().parents[2] / "app/web/p10.js", media_type="text/javascript")

    @application.get("/assets/p11.js")
    def p11_script():
        return FileResponse(Path(__file__).resolve().parents[2] / "app/web/p11.js", media_type="text/javascript")

    @application.get("/assets/profit-bridge.js")
    def profit_bridge_script():
        return FileResponse(Path(__file__).resolve().parents[2] / "app/web/profit-bridge.js", media_type="text/javascript")

    @application.get("/assets/pdf-prescan-ui.js")
    def pdf_prescan_script():
        return FileResponse(Path(__file__).resolve().parents[2] / "app/web/pdf-prescan-ui.js", media_type="text/javascript")

    @application.get("/v1/financial-analysis/demo-reports")
    def demo_reports() -> dict:
        return {"reports":[{"id":key,"label":label,"source_status":status} for key,(label,path,status) in demo_registry.items() if path.exists()]}

    @application.get("/v1/financial-analysis/demo-reports/{report_id}")
    def demo_report(report_id: str) -> dict:
        item=demo_registry.get(report_id)
        if item is None or not item[1].exists():
            raise HTTPException(status_code=404,detail="Demo report unavailable")
        payload=json.loads(item[1].read_text(encoding="utf-8"))
        notes=payload.get("note_candidates",[])
        notes_path=Path(__file__).resolve().parents[2]/"benchmarks/p6_pnj_note_candidates.json"
        if report_id=="pnj-2021-2025" and notes_path.exists():
            note_payload=json.loads(notes_path.read_text(encoding="utf-8"))
            notes=[{**candidate,"document_hash":note_payload["source_sha256"],
                    "audit_status":note_payload["audit_status"]} for candidate in note_payload.get("candidates",[])]
        return {"source_status":item[2],"report":payload.get("report",payload),
                "diagnostics":payload.get("diagnostics",[]),"note_candidates":notes}

    @application.get("/v1/financial-analysis/demo-sources/{document_hash}")
    def demo_source(document_hash: str):
        if len(document_hash)!=64 or any(ch not in "0123456789abcdef" for ch in document_hash):
            raise HTTPException(status_code=404,detail="Demo source unavailable")
        root=Path(__file__).resolve().parents[2]
        allowed=[*sorted((root/".cache/pnj_precode").glob("*.pdf")),root/".cache/holdout_new/vhc.pdf"]
        for candidate in allowed:
            if candidate.is_file() and hashlib.sha256(candidate.read_bytes()).hexdigest()==document_hash:
                return FileResponse(candidate,media_type="application/pdf",filename=candidate.name)
        raise HTTPException(status_code=404,detail="Demo source unavailable")

    def serialized(function):
        @wraps(function)
        def inner(*args, **kwargs):
            with lock:
                return function(*args, **kwargs)
        return inner

    @application.post("/v1/documents/ingest", response_model=IngestResponse)
    @serialized
    def ingest(
        file: Annotated[UploadFile, File(description="TT200-compatible .xlsx financial statement")],
        accounting_regime: Annotated[str | None, Form()] = None,
        data_origin: Annotated[Literal["SYNTHETIC", "REAL"] | None, Form()] = None,
        company_id: Annotated[str, Form()] = "UNAVAILABLE",
    ) -> IngestResponse:
        if not file.filename or not file.filename.lower().endswith(".xlsx"):
            raise HTTPException(status_code=415, detail="Upload an .xlsx file")
        content = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="XLSX exceeds 20 MiB limit")
        if not is_zipfile(io.BytesIO(content)):
            raise HTTPException(status_code=422, detail="Invalid XLSX archive")
        document_hash = hashlib.sha256(content).hexdigest()
        upload_dir.mkdir(parents=True, exist_ok=True)
        destination = upload_dir / f"{document_hash}.xlsx"
        index_path = upload_dir / f"{document_hash}.index.json"
        created = not destination.exists()
        if created or hashlib.sha256(destination.read_bytes()).hexdigest() != document_hash:
            destination.write_bytes(content)
        try:
            result = ingest_excel(
                destination, accounting_regime=accounting_regime,
                data_origin=data_origin, company_id=company_id,
            )
            if not result.facts:
                raise ValueError("No supported financial facts found")
        except (ValueError, InvalidFileException, BadZipFile, KeyError) as error:
            if created:
                destination.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail=f"Cannot ingest workbook: {error}") from error
        index = DocumentIndex.from_ingestion(result)
        if index_path.exists():
            try:
                old_index = DocumentIndex.from_json_dict(json.loads(index_path.read_text(encoding="utf-8")))
                old_context = old_index.result.facts[0].document
                new_context = result.facts[0].document
                if (old_context.accounting_regime, old_context.data_origin, old_context.company_id) != (
                    new_context.accounting_regime, new_context.data_origin, new_context.company_id
                ):
                    raise HTTPException(status_code=409, detail="Metadata conflicts with existing document hash")
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                pass  # replace a damaged local index with a fresh parse of the same XLSX
        staging = upload_dir / f"{document_hash}.index.json.tmp"
        staging.write_text(json.dumps(index.to_json_dict(), ensure_ascii=False), encoding="utf-8")
        staging.replace(index_path)
        application.state.documents[document_hash] = index
        return IngestResponse(
            document_hash=document_hash, fact_count=len(result.facts),
            note_count=len(result.notes), sheet_count=len(result.headers),
            states=dict(Counter(fact.state.value for fact in result.facts)),
        )

    @application.post("/v1/documents/prescan")
    @serialized
    def prescan_pdf(file: Annotated[UploadFile, File(description="Complete or partial financial-statement PDF")]) -> dict:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=415, detail="Upload a .pdf file")
        upload_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        total = 0
        header = b""
        try:
            with tempfile.NamedTemporaryFile(dir=upload_dir, prefix=".prescan-upload-", suffix=".pdf", delete=False) as staging_file:
                scratch = Path(staging_file.name)
                while chunk := file.file.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_PDF_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="PDF exceeds 30 MiB limit")
                    if not header:
                        header = chunk[:5]
                    digest.update(chunk)
                    staging_file.write(chunk)
        except BaseException:
            if "scratch" in locals():
                scratch.unlink(missing_ok=True)
            raise
        if header != b"%PDF-":
            scratch.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail="Invalid PDF signature")
        document_hash = digest.hexdigest()
        source = upload_dir / f"{document_hash}.pdf"
        created = not source.exists()
        if not created and hashlib.sha256(source.read_bytes()).hexdigest() != document_hash:
            scratch.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail="Stored PDF hash mismatch")
        if created:
            scratch.replace(source)
        else:
            scratch.unlink(missing_ok=True)
        record = upload_dir / f"{document_hash}.prescan.json"
        if record.is_file():
            try:
                cached = json.loads(record.read_text(encoding="utf-8"))
                if cached.get("document_hash") == document_hash and cached.get("scan_version") == PRESCAN_VERSION:
                    return cached
            except (OSError, json.JSONDecodeError):
                pass
        try:
            inventory = scan_pdf(source)
        except (RuntimeError, ValueError, OSError) as error:
            if created:
                source.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail="Cannot read PDF pages") from error
        if not inventory["page_count"]:
            raise HTTPException(status_code=422, detail="PDF has no pages")
        payload = {"document_hash": document_hash, "scan_version": PRESCAN_VERSION, **inventory}
        prescan_id = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        payload["prescan_id"] = prescan_id
        staging = upload_dir / f"{document_hash}.prescan.json.tmp"
        staging.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        staging.replace(upload_dir / f"{document_hash}.prescan.json")
        return payload

    @application.get("/v1/documents/{document_hash}/prescan-page/{page_number}")
    @serialized
    def prescan_page(document_hash: str, page_number: int) -> Response:
        if len(document_hash) != 64 or any(char not in "0123456789abcdef" for char in document_hash):
            raise HTTPException(status_code=404, detail="Pre-scanned PDF unavailable")
        source = upload_dir / f"{document_hash}.pdf"
        record = upload_dir / f"{document_hash}.prescan.json"
        if not record.is_file() or not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != document_hash:
            raise HTTPException(status_code=404, detail="Pre-scanned PDF unavailable")
        try:
            import fitz
            with fitz.open(source) as pdf:
                if not 1 <= page_number <= len(pdf):
                    raise HTTPException(status_code=422, detail="Page outside PDF")
                image = pdf[page_number - 1].get_pixmap(matrix=fitz.Matrix(0.7, 0.7), alpha=False).tobytes("png")
        except (RuntimeError, OSError) as error:
            raise HTTPException(status_code=422, detail="Cannot render PDF page") from error
        return Response(image, media_type="image/png", headers={"Cache-Control": "no-store"})

    @application.post("/v1/documents/{document_hash}/extract-confirmed")
    @serialized
    def extract_confirmed(document_hash: str, request: ConfirmedPdfExtraction) -> dict:
        if len(document_hash) != 64 or any(char not in "0123456789abcdef" for char in document_hash):
            raise HTTPException(status_code=404, detail="Pre-scan unavailable")
        source = upload_dir / f"{document_hash}.pdf"
        prescan_path = upload_dir / f"{document_hash}.prescan.json"
        if not source.is_file() or not prescan_path.is_file():
            raise HTTPException(status_code=404, detail="Pre-scan unavailable")
        if hashlib.sha256(source.read_bytes()).hexdigest() != document_hash:
            raise HTTPException(status_code=409, detail="Stored PDF hash mismatch")
        try:
            inventory = json.loads(prescan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise HTTPException(status_code=409, detail="Stored pre-scan invalid") from error
        if (inventory.get("prescan_id") != request.prescan_id or inventory.get("document_hash") != document_hash
                or inventory.get("scan_version") != PRESCAN_VERSION):
            raise HTTPException(status_code=409, detail="Stale or mismatched pre-scan")
        if request.fiscal_start > request.fiscal_end or request.fiscal_end.year - request.fiscal_start.year > 1:
            raise HTTPException(status_code=422, detail="Invalid fiscal period")
        dimensions = {page["page_number"]: page for page in inventory["pages"]}
        regions: dict[int, list[dict]] = {}
        multipliers = {"đồng": "1", "nghìn đồng": "1000", "triệu đồng": "1000000", "tỷ đồng": "1000000000"}
        for item in request.pages:
            page = dimensions.get(item.page_number)
            if page is None:
                raise HTTPException(status_code=422, detail="Confirmed page outside PDF")
            observed = [candidate for candidate in inventory["candidates"]
                        if candidate["page_number"] == item.page_number and candidate["region"] == item.region
                        and candidate["confidence"] == "HIGH"]
            for candidate in observed:
                if candidate.get("report_scope") and candidate["report_scope"] != request.report_scope.value:
                    raise HTTPException(status_code=422, detail="Report scope contradicts the source page")
                if candidate.get("unit_label") and candidate["unit_label"] != request.unit_label:
                    raise HTTPException(status_code=422, detail="Currency unit contradicts the source page")
                years = candidate.get("year_headers", [])
                if len(years) >= 2 and str(request.fiscal_end.year) not in years:
                    raise HTTPException(status_code=422, detail="Fiscal year contradicts the source page")
            box = region_box(page["width"], page["height"], item.region)
            for old in regions.get(item.page_number, []):
                a = old["region_bbox"]
                if min(a[2], box[2]) > max(a[0], box[0]) and min(a[3], box[3]) > max(a[1], box[1]):
                    raise HTTPException(status_code=422, detail="Overlapping confirmed regions")
            regions.setdefault(item.page_number, []).append({"form_code": item.form_code, "region": item.region,
                "region_bbox": box, "page_height": page["height"], "unit_label": request.unit_label,
                "unit_multiplier": multipliers[request.unit_label]})
        selected = sorted(regions)
        index_path = upload_dir / f"{document_hash}.index.json"
        previous = None
        if index_path.is_file():
            try:
                previous = DocumentIndex.from_json_dict(json.loads(index_path.read_text(encoding="utf-8")))
                context = previous.result.facts[0].document
            except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
                raise HTTPException(status_code=409, detail="Stored index invalid") from error
            if (context.company_name, context.company_id, context.report_scope, context.fiscal_period_start,
                context.fiscal_period_end) != (request.company_name, request.company_id, request.report_scope,
                                               request.fiscal_start, request.fiscal_end):
                raise HTTPException(status_code=409, detail="Metadata conflicts with existing index")
            if any(fact.state != FactState.EXTRACTED and any(
                isinstance(e.locator, PDFLocator) and e.locator.page_number in regions for e in fact.evidence
            ) for fact in previous.result.facts):
                raise HTTPException(status_code=409, detail="Cannot replace reviewed facts")
        spec = PdfDocumentSpec(company_name=request.company_name, company_id=request.company_id,
                               document_hash=document_hash, source_path=str(source), report_scope=request.report_scope,
                               accounting_regime="TT200", fiscal_start=request.fiscal_start, fiscal_end=request.fiscal_end)
        # Convert only confirmed pages. Small gaps may be included in a bounded
        # conversion, but the parser's confirmed-region allowlist excludes them.
        ranges = []
        first = last = selected[0]
        for number in selected[1:]:
            if number - last <= 2 and number - first < 30:
                last = number
            else:
                ranges.append((first, last))
                first = last = number
        ranges.append((first, last))
        combined = {"status": "SUCCESS", "ocr": request.ocr, "document": {"texts": [], "tables": []}}
        with tempfile.TemporaryDirectory(prefix="pdf-confirmed-") as temp:
            for start, end in ranges:
                try:
                    payload = pdf_converter(source, Path(temp) / f"docling-{start}-{end}.json", start, end, request.ocr)
                except RuntimeError as error:
                    raise HTTPException(status_code=503, detail=str(error)) from error
                if str(payload.get("status", "")).split(".")[-1] != "SUCCESS" or payload.get("errors"):
                    raise HTTPException(status_code=422, detail="Incomplete PDF conversion")
                for key in ("texts", "tables"):
                    combined["document"][key].extend(payload.get("document", {}).get(key, []))
                for key in ("spatial_ocr", "refined_code_groups"):
                    combined.setdefault(key, {}).update(payload.get(key, {}))
        result = extract_docling_tables(combined, spec, extended_codes=request.extended_codes,
                                        confirmed_regions=regions)
        if not result.facts:
            raise HTTPException(status_code=422, detail={"message": "No supported facts on confirmed pages",
                                                          "diagnostics": result.diagnostics})
        if previous is not None:
            retained = [fact for fact in previous.result.facts if not any(
                isinstance(e.locator, PDFLocator) and e.locator.page_number in regions for e in fact.evidence)]
            result = PdfIngestionResult(document_hash=document_hash, facts=retained + result.facts,
                notes=previous.result.notes, diagnostics=result.diagnostics, review_history=previous.result.review_history)
        index = DocumentIndex.from_ingestion(result, supplemental_facts=previous.supplemental_facts if previous else ())
        staging = upload_dir / f"{document_hash}.index.json.tmp"
        staging.write_text(json.dumps(index.to_json_dict(), ensure_ascii=False), encoding="utf-8")
        staging.replace(index_path)
        application.state.documents[document_hash] = index
        map_path = upload_dir / f"{document_hash}.confirmed-map.json"
        map_path.write_text(json.dumps({"prescan_id": request.prescan_id, "reviewer": request.reviewer,
            "confirmed_at": datetime.now(timezone.utc).isoformat(), "pages": [p.model_dump() for p in request.pages]},
            ensure_ascii=False), encoding="utf-8")
        return {"document_hash": document_hash, "fact_count": len(result.facts),
                "states": dict(Counter(f.state.value for f in result.facts)),
                "selected_pages": selected, "diagnostics": list(result.diagnostics)}

    @application.post("/v1/documents/ingest-pdf", response_model=PdfIngestResponse)
    @serialized
    def ingest_pdf(
        file: Annotated[UploadFile, File(description="PDF financial statement")],
        company_name: Annotated[str, Form(min_length=1, max_length=200)],
        company_id: Annotated[str, Form(min_length=1, max_length=80)],
        report_scope: Annotated[ReportScope, Form()],
        fiscal_start: Annotated[date, Form()],
        fiscal_end: Annotated[date, Form()],
        page_start: Annotated[int, Form(ge=1, le=10000)],
        page_end: Annotated[int, Form(ge=1, le=10000)],
        ocr: Annotated[bool, Form()] = False,
        extended_codes: Annotated[bool, Form()] = False,
    ) -> PdfIngestResponse:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=415, detail="Upload a .pdf file")
        if fiscal_start > fiscal_end or fiscal_end.year - fiscal_start.year > 1:
            raise HTTPException(status_code=422, detail="Invalid fiscal period")
        if page_end < page_start or page_end - page_start >= 30:
            raise HTTPException(status_code=422, detail="Choose an ordered page range of at most 30 pages")
        content = file.file.read(MAX_PDF_UPLOAD_BYTES + 1)
        if len(content) > MAX_PDF_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="PDF exceeds 30 MiB limit")
        if not content.startswith(b"%PDF-"):
            raise HTTPException(status_code=422, detail="Invalid PDF signature")
        document_hash = hashlib.sha256(content).hexdigest()
        upload_dir.mkdir(parents=True, exist_ok=True)
        destination = upload_dir / f"{document_hash}.pdf"
        index_path = upload_dir / f"{document_hash}.index.json"
        spec = PdfDocumentSpec(company_name=company_name, company_id=company_id,
                               document_hash=document_hash, source_path=str(destination),
                               report_scope=report_scope, accounting_regime="TT200",
                               fiscal_start=fiscal_start, fiscal_end=fiscal_end)
        previous = None
        if index_path.exists():
            try:
                previous = DocumentIndex.from_json_dict(json.loads(index_path.read_text(encoding="utf-8")))
                context = previous.result.facts[0].document
                old = (context.company_name, context.company_id, context.report_scope, context.fiscal_period_start, context.fiscal_period_end)
                if old != (company_name, company_id, report_scope, fiscal_start, fiscal_end):
                    raise HTTPException(status_code=409, detail="Metadata conflicts with existing document hash")
                if not destination.exists() or hashlib.sha256(destination.read_bytes()).hexdigest() != document_hash:
                    raise HTTPException(status_code=409, detail="Stored document hash mismatch")
                if any(fact.state != FactState.EXTRACTED and any(
                        e.locator.source_kind == "pdf" and page_start <= e.locator.page_number <= page_end
                        for e in fact.evidence) for fact in previous.result.facts):
                    raise HTTPException(status_code=409, detail="Cannot overwrite reviewed facts on the requested pages")
            except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
                raise HTTPException(status_code=409, detail="Stored index is invalid") from error
        with tempfile.TemporaryDirectory(prefix="pdf-ingest-") as temp:
            source = Path(temp) / "source.pdf"
            source.write_bytes(content)
            try:
                payload = pdf_converter(source, Path(temp) / "docling.json", page_start, page_end, ocr)
                if str(payload.get("status", "")).split(".")[-1] != "SUCCESS" or payload.get("errors"):
                    raise ValueError("conversion was incomplete")
                result = extract_docling_tables(payload, spec, extended_codes=extended_codes)
            except RuntimeError as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            except (ValueError, KeyError, TypeError) as error:
                raise HTTPException(status_code=422, detail=f"PDF conversion failed: {error}") from error
        if not result.facts:
            raise HTTPException(status_code=422, detail={"message": "No supported financial facts found", "diagnostics": result.diagnostics})
        if any(not page_start <= evidence.locator.page_number <= page_end
               for fact in result.facts for evidence in fact.evidence):
            raise HTTPException(status_code=422, detail="Converter returned a fact outside the requested page range")
        if previous is not None:
            retained = [fact for fact in previous.result.facts
                        if not any(e.locator.source_kind == "pdf" and page_start <= e.locator.page_number <= page_end
                                   for e in fact.evidence)]
            result = PdfIngestionResult(document_hash=document_hash, facts=retained + result.facts,
                                        notes=previous.result.notes, diagnostics=result.diagnostics,
                                        review_history=previous.result.review_history)
        index = DocumentIndex.from_ingestion(result, supplemental_facts=previous.supplemental_facts if previous else ())
        destination.write_bytes(content)
        staging = upload_dir / f"{document_hash}.index.json.tmp"
        staging.write_text(json.dumps(index.to_json_dict(), ensure_ascii=False), encoding="utf-8")
        staging.replace(index_path)
        application.state.documents[document_hash] = index
        return PdfIngestResponse(document_hash=document_hash, fact_count=len(result.facts),
                                 states=dict(Counter(fact.state.value for fact in result.facts)),
                                 diagnostics=list(result.diagnostics), page_start=page_start, page_end=page_end)

    def load_index(document_hash: str) -> DocumentIndex:
        index_path = upload_dir / f"{document_hash}.index.json"
        if index_path.exists():
            try:
                index = DocumentIndex.from_json_dict(json.loads(index_path.read_text(encoding="utf-8")))
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
                raise HTTPException(status_code=409, detail="Stored index is invalid") from error
            if index.result.document_hash != document_hash:
                raise HTTPException(status_code=409, detail="Stored index hash mismatch")
            suffix = ".pdf" if isinstance(index.result, PdfIngestionResult) else ".xlsx"
            source = upload_dir / f"{document_hash}{suffix}"
            if not source.exists() or hashlib.sha256(source.read_bytes()).hexdigest() != document_hash:
                raise HTTPException(status_code=409, detail="Stored document hash mismatch")
            application.state.documents[document_hash] = index
        else:
            index=application.state.documents.get(document_hash)
            if index is None:
                raise HTTPException(status_code=404, detail="Document not ingested")
        return index

    def assert_pdf_source(document_hash: str) -> Path:
        source=upload_dir/f"{document_hash}.pdf"
        if not source.exists() or hashlib.sha256(source.read_bytes()).hexdigest()!=document_hash:
            raise HTTPException(status_code=409,detail="Stored PDF hash mismatch")
        return source

    def persist_index(document_hash: str, index: DocumentIndex) -> None:
        staging=upload_dir/f"{document_hash}.index.json.tmp"
        staging.write_text(json.dumps(index.to_json_dict(),ensure_ascii=False),encoding="utf-8")
        staging.replace(upload_dir/f"{document_hash}.index.json")
        application.state.documents[document_hash]=index

    @application.post("/v1/documents/{document_hash}/extract-supplemental")
    @serialized
    def extract_supplemental(document_hash: str, request: SupplementalExtractRequest) -> dict:
        index=load_index(document_hash)
        if not isinstance(index.result,PdfIngestionResult):
            raise HTTPException(status_code=409,detail="Requires a PDF index")
        source=assert_pdf_source(document_hash)
        if request.page_end<request.page_start or request.page_end-request.page_start>=30 or request.page_end>len(PdfReader(source).pages):
            raise HTTPException(status_code=422,detail="Invalid note page range")
        with tempfile.TemporaryDirectory(prefix="note-extract-") as temp:
            try:
                payload=pdf_converter(source,Path(temp)/"docling.json",request.page_start,request.page_end,request.ocr)
                if str(payload.get("status","")).split(".")[-1]!="SUCCESS" or payload.get("errors"):
                    raise ValueError("Incomplete note conversion")
                candidates,diagnostics=extract_note_candidates(payload,fiscal_year=request.fiscal_year)
            except RuntimeError as error:
                raise HTTPException(status_code=503,detail=str(error)) from error
            except (ValueError,KeyError,TypeError) as error:
                raise HTTPException(status_code=422,detail=str(error)) from error
        valid=[]
        for candidate in candidates:
            page=candidate["locator"]["page_number"]
            if request.page_start<=page<=request.page_end:
                valid.append(candidate)
            else:
                diagnostics.append(f"Candidate page {page} outside requested range")
        return {"document_hash":document_hash,"candidates":valid,"diagnostics":diagnostics,
                "review_required":True}

    @application.get("/v1/documents/{document_hash}/supplemental-facts")
    @serialized
    def list_supplemental(document_hash: str) -> dict:
        index=load_index(document_hash)
        return {"document_hash":document_hash,"facts":[item.model_dump(mode="json") for item in index.supplemental_facts]}

    @application.post("/v1/documents/{document_hash}/supplemental-facts")
    @serialized
    def propose_supplemental(document_hash: str, request: SupplementalProposalRequest) -> dict:
        index=load_index(document_hash)
        if not isinstance(index.result,PdfIngestionResult):
            raise HTTPException(status_code=409,detail="Supplemental PDF proposal requires a PDF index")
        source=assert_pdf_source(document_hash)
        if request.locator.page_number>len(PdfReader(source).pages):
            raise HTTPException(status_code=422,detail="Supplemental page outside source PDF")
        evidence=Evidence(document_hash=document_hash,locator=request.locator,observed_text=request.observed_text)
        payload={"document_hash":document_hash,"concept":request.concept,"fiscal_year":request.fiscal_year,
                 "value_vnd":str(request.value_vnd),"parent_vas_code":request.parent_vas_code,
                 "evidence":evidence.model_dump(mode="json")}
        item=SupplementalFact(fact_id=supplemental_id(payload),document_hash=document_hash,concept=request.concept,
                              fiscal_year=request.fiscal_year,value_vnd=request.value_vnd,
                              parent_vas_code=request.parent_vas_code,anchor_relation=request.anchor_relation,
                              evidence=evidence,note_ref=request.note_ref)
        relation=check_anchor(index,item)
        if relation.startswith("FAIL") or relation.startswith("INVALID") or relation=="MIXED_SCOPE":
            raise HTTPException(status_code=422,detail=f"Anchor check: {relation}")
        if any(x.concept==item.concept and x.fiscal_year==item.fiscal_year and x.reconciliation_status!="REJECTED" for x in index.supplemental_facts):
            raise HTTPException(status_code=409,detail="Supplemental concept/year already proposed")
        item=item.model_copy(update={"reconciliation_status":relation})
        updated=DocumentIndex.from_ingestion(index.result,supplemental_facts=(*index.supplemental_facts,item))
        persist_index(document_hash,updated)
        return item.model_dump(mode="json")

    @application.post("/v1/documents/{document_hash}/signoff-supplemental")
    @serialized
    def signoff_supplemental(document_hash: str, request: SupplementalSignoffRequest) -> dict:
        index=load_index(document_hash)
        assert_pdf_source(document_hash)
        if not request.reviewer.strip():
            raise HTTPException(status_code=422,detail="Reviewer name required")
        matches=[(i,item) for i,item in enumerate(index.supplemental_facts) if item.fact_id==request.fact_id]
        if len(matches)!=1:
            raise HTTPException(status_code=409,detail="Supplemental fact missing or ambiguous")
        position,item=matches[0]
        if item.state!=FactState.EXTRACTED or item.value_vnd!=request.confirmed_value_vnd:
            raise HTTPException(status_code=409,detail="Fact already reviewed or confirmed value differs")
        relation=check_anchor(index,item)
        if relation not in {"PASS","SEMANTIC_ONLY"}:
            raise HTTPException(status_code=409,detail=f"Anchor check: {relation}")
        if request.decision=="REJECT":
            replacement=item.model_copy(update={"reconciliation_status":"REJECTED","reviewer":request.reviewer.strip(),
                                                "reviewed_at_utc":datetime.now(timezone.utc)})
        else:
            replacement=item.model_copy(update={"state":FactState.VERIFIED,"reconciliation_status":relation,
                                                "reviewer":request.reviewer.strip(),"reviewed_at_utc":datetime.now(timezone.utc)})
        facts=list(index.supplemental_facts);facts[position]=replacement
        group=check_receivable_group(index,tuple(facts),item.fiscal_year)
        if group=="FAIL_GROSS_NET_MISMATCH":
            raise HTTPException(status_code=409,detail="Receivable gross/provision does not reconcile to B01:131")
        subset=check_receivable_subsets(tuple(facts),item.fiscal_year)
        if subset=="FAIL_OVERDUE_EXCEEDS_GROSS":
            raise HTTPException(status_code=409,detail="Overdue trade receivables exceed gross trade receivables")
        cash_check=check_cash_group(index,tuple(facts),item.fiscal_year)
        if cash_check.startswith("FAIL"):
            raise HTTPException(status_code=409,detail=f"Cash note reconciliation: {cash_check}")
        updated=DocumentIndex.from_ingestion(index.result,supplemental_facts=tuple(facts))
        persist_index(document_hash,updated)
        return {"fact":replacement.model_dump(mode="json"),"receivable_group_check":group,
                "receivable_subset_check":subset,"cash_group_check":cash_check}

    @application.get("/v1/documents/{document_hash}/pdf-facts")
    @serialized
    def list_pdf_facts(document_hash: str) -> dict:
        if len(document_hash) != 64 or any(ch not in "0123456789abcdef" for ch in document_hash):
            raise HTTPException(status_code=422, detail="Invalid document hash")
        index = load_index(document_hash)
        if not isinstance(index.result, PdfIngestionResult):
            raise HTTPException(status_code=409, detail="Document is not a PDF index")
        return {"document_hash": document_hash,
                "facts": [{"fact_id": pdf_fact_id(fact), "fact": fact.model_dump(mode="json")} for fact in index.result.facts],
                "review_history": list(index.result.review_history)}

    @application.post("/v1/documents/{document_hash}/signoff")
    @application.post("/v1/documents/{document_hash}/pdf-fact-reviews")
    @serialized
    def review_pdf_fact(document_hash: str, request: PdfFactReviewRequest) -> dict:
        if len(document_hash) != 64 or any(ch not in "0123456789abcdef" for ch in document_hash):
            raise HTTPException(status_code=422, detail="Invalid document hash")
        index = load_index(document_hash)
        if not isinstance(index.result, PdfIngestionResult):
            raise HTTPException(status_code=409, detail="Document is not a PDF index")
        source = upload_dir / f"{document_hash}.pdf"
        if not source.exists() or hashlib.sha256(source.read_bytes()).hexdigest() != document_hash:
            raise HTTPException(status_code=409, detail="Stored document hash mismatch")
        matches = [i for i, fact in enumerate(index.result.facts) if pdf_fact_id(fact) == request.fact_id]
        if len(matches) != 1:
            raise HTTPException(status_code=409, detail="Fact is missing, ambiguous or has changed; reload it")
        position = matches[0]
        fact = index.result.facts[position]
        if fact.state != FactState.EXTRACTED:
            raise HTTPException(status_code=409, detail="Fact has already been reviewed")
        if (fact.normalized_value != request.confirmed_normalized_value or fact.currency != request.confirmed_currency
                or fact.period != request.confirmed_period):
            raise HTTPException(status_code=409, detail="Confirmed value, currency or period differs from extracted fact")
        if request.decision == "REJECT" and not request.comment.strip():
            raise HTTPException(status_code=422, detail="OCR rejection requires a reason")
        updated = (fact.transition(FactState.VERIFIED, source_checked=True)
                   if request.decision == "CONFIRM" else FinancialFact.model_validate({
                       **fact.model_dump(), "state": FactState.REJECTED_OCR_ERROR,
                       "verification": VerificationRecord(source_checked=True).model_dump()}))
        event = {"fact_id": request.fact_id, "decision": request.decision, "reviewer": request.reviewer.strip(),
                 "reviewed_at_utc": datetime.now(timezone.utc).isoformat(), "comment": request.comment,
                 "confirmed_normalized_value": str(request.confirmed_normalized_value),
                 "confirmed_currency": request.confirmed_currency, "confirmed_period": request.confirmed_period.model_dump(mode="json"),
                 "result_fact_id": pdf_fact_id(updated)}
        facts = list(index.result.facts)
        facts[position] = updated
        result = PdfIngestionResult(document_hash=document_hash, facts=facts, headers=index.result.headers,
                                    notes=index.result.notes, diagnostics=index.result.diagnostics,
                                    review_history=index.result.review_history + (event,))
        updated_index = DocumentIndex.from_ingestion(result,supplemental_facts=index.supplemental_facts)
        staging = upload_dir / f"{document_hash}.index.json.tmp"
        staging.write_text(json.dumps(updated_index.to_json_dict(), ensure_ascii=False), encoding="utf-8")
        staging.replace(upload_dir / f"{document_hash}.index.json")
        application.state.documents[document_hash] = updated_index
        return {"document_hash": document_hash, "fact_id": pdf_fact_id(updated), "state": updated.state.value, "review": event}

    def save_reviewed_pdf(index: DocumentIndex, facts: list[FinancialFact], events: list[dict]) -> None:
        original = index.result
        result = PdfIngestionResult(document_hash=original.document_hash, facts=facts, headers=original.headers,
                                    notes=original.notes, diagnostics=original.diagnostics,
                                    review_history=original.review_history + tuple(events))
        updated = DocumentIndex.from_ingestion(result, supplemental_facts=index.supplemental_facts)
        path = upload_dir / f"{original.document_hash}.index.json"
        staging = upload_dir / f"{original.document_hash}.index.json.tmp"
        staging.write_text(json.dumps(updated.to_json_dict(), ensure_ascii=False), encoding="utf-8")
        staging.replace(path)
        application.state.documents[original.document_hash] = updated

    def pdf_index(document_hash: str) -> DocumentIndex:
        if len(document_hash) != 64 or any(ch not in "0123456789abcdef" for ch in document_hash):
            raise HTTPException(status_code=422, detail="Invalid document hash")
        index = load_index(document_hash)
        source = upload_dir / f"{document_hash}.pdf"
        if not isinstance(index.result, PdfIngestionResult) or not source.is_file():
            raise HTTPException(status_code=409, detail="Document is not an indexed PDF")
        if hashlib.sha256(source.read_bytes()).hexdigest() != document_hash:
            raise HTTPException(status_code=409, detail="Stored document hash mismatch")
        return index

    def audit_groups(index: DocumentIndex) -> list[dict]:
        grouped: dict[tuple[str, int], list[FinancialFact]] = {}
        for fact in index.result.facts:
            if fact.line_code.form_code in {"B01-DN", "B02-DN", "B03-DN"}:
                grouped.setdefault((fact.line_code.form_code, int(fact.period.fiscal_year_label.removeprefix("FY"))), []).append(fact)
        output = []
        for (form, year), facts in sorted(grouped.items()):
            totals = {code: [f for f in facts if f.line_code.code == code]
                      for code in ("270", "300", "400")}
            unique = all(len(totals[code]) == 1 and totals[code][0].normalized_value is not None
                         and totals[code][0].state != FactState.REJECTED_OCR_ERROR for code in totals)
            residual = (totals["270"][0].normalized_value - totals["300"][0].normalized_value
                        - totals["400"][0].normalized_value) if unique else None
            checks = reconcile_statement(facts, form, year)
            passing = [check for check in checks if check["status"] == "MATH_CONSISTENT"]
            failing = [check for check in checks if check["status"] in {"MISMATCH", "DUPLICATE_INPUT", "AMBIGUOUS_SIGN"}]
            if form != "B01-DN":
                residual = Decimal(passing[0]["residual_vnd"]) if passing and not failing else None
            pending = [f for f in facts if f.state == FactState.EXTRACTED]
            counts = Counter(f.line_code.code for f in facts)
            reconciled_codes = {code for check in passing for code in check["input_codes"]
                                if code not in check["omitted_zero_codes"]}
            def flags(f):
                reasons = []
                if counts[f.line_code.code] > 1:
                    reasons.append("DUPLICATE_CODE")
                if not f.line_code.code:
                    reasons.append("MISSING_CODE")
                if "(" in str(f.raw_value):
                    reasons.append("PARENTHESIZED_VALUE_REVIEW")
                if any("ghép" in (getattr(e.locator, "region", "") or "").casefold() for e in f.evidence):
                    reasons.append("MERGED_OCR_SOURCE")
                if not any(isinstance(e.locator, PDFLocator) and e.locator.bounding_box
                           for e in f.evidence):
                    reasons.append("MISSING_VALUE_BBOX")
                if f.line_code.code not in reconciled_codes:
                    reasons.append("NOT_IN_MATCHED_IDENTITY")
                # OCR confidence is not recorded per financial fact today.
                reasons.append("OCR_CONFIDENCE_UNAVAILABLE")
                return reasons
            def batch_safe(f):
                return bool(f.normalized_value is not None and
                            not (set(flags(f)) - {"OCR_CONFIDENCE_UNAVAILABLE"}))
            safe_pending = [f for f in pending if batch_safe(f)]
            output.append({"form_code": form, "fiscal_year": year,
                           "reconciliation_residual_vnd": str(residual) if residual is not None else None,
                           "batch_eligible": residual == 0 and bool(safe_pending) and not failing,
                           "checks": checks,
                           "pending_count": len(pending),
                           "batch_safe_count": len(safe_pending),
                           "individual_review_count": len(pending) - len(safe_pending),
                           "pending_rows": [{"fact_id": pdf_fact_id(f), "value_vnd": str(f.normalized_value),
                                             "code": f.line_code.code, "label": f.original_label,
                                             "warnings": flags(f), "default_selected": False,
                                             "review_bucket": "ELIGIBLE_FOR_BATCH" if batch_safe(f) else "INDIVIDUAL_REVIEW",
                                             "pages": sorted({e.locator.page_number for e in f.evidence
                                                              if isinstance(e.locator, PDFLocator)})}
                                            for f in pending]})
        return output

    @application.get("/v1/documents/{document_hash}/audit-queue")
    @serialized
    def audit_queue(document_hash: str) -> dict:
        index = pdf_index(document_hash)
        rows = []
        for fact in index.result.facts:
            evidence = next((e for e in fact.evidence if isinstance(e.locator, PDFLocator)), fact.evidence[0])
            rows.append({"fact_id": pdf_fact_id(fact), "state": fact.state.value,
                         "fiscal_year": int(fact.period.fiscal_year_label.removeprefix("FY")),
                         "form_code": fact.line_code.form_code, "code": fact.line_code.code,
                         "label": fact.original_label, "raw_value": fact.raw_value,
                         "normalized_value_vnd": str(fact.normalized_value) if fact.normalized_value is not None else None,
                         "currency": fact.currency, "unit": fact.unit_label,
                         "period": fact.period.model_dump(mode="json"),
                         "evidence": evidence.model_dump(mode="json")})
        rows.sort(key=lambda x:(x["state"] != "EXTRACTED", x["code"] not in {"123","110","320"}
                                if index.result.facts[0].document.company_id == "VHC" else True,
                                -x["fiscal_year"], x["form_code"], x["code"]))
        with open(upload_dir / f"{document_hash}.pdf", "rb") as stream:
            page_count = len(PdfReader(stream).pages)
        return {"document_hash": document_hash, "page_count": page_count, "facts": rows,
                "reconciled_groups": audit_groups(index),
                "statement_checks": [{"form_code": form, "fiscal_year": year,
                    "checks": reconcile_statement(index.result.facts, form, year)}
                    for form, year in sorted({(f.line_code.form_code, int(f.period.fiscal_year_label.removeprefix("FY")))
                                              for f in index.result.facts}) if form in {"B01-DN", "B02-DN", "B03-DN"}],
                "supplemental_facts": [f.model_dump(mode="json") for f in index.supplemental_facts],
                "review_history": list(index.result.review_history)}

    def render_audit_page(document_hash: str, page_number: int, locator: PDFLocator | None = None) -> Response:
        try:
            import fitz
            with fitz.open(upload_dir / f"{document_hash}.pdf") as pdf:
                page = pdf[page_number - 1]
                if locator is not None and locator.bounding_box is not None:
                    x0, y0, x1, y1 = locator.bounding_box
                    if locator.coordinate_origin == "BOTTOMLEFT":
                        y0, y1 = page.rect.height - y0, page.rect.height - y1
                    rect = fitz.Rect(x0, y0, x1, y1) & page.rect
                    if not rect.is_empty:
                        page.draw_rect(rect, color=(0.95, 0.2, 0.12), width=2, overlay=True)
                image = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).tobytes("png")
        except (IndexError, RuntimeError, OSError) as error:
            raise HTTPException(status_code=422, detail="Cannot render source page") from error
        return Response(image, media_type="image/png", headers={"Cache-Control": "no-store"})

    @application.get("/v1/documents/{document_hash}/audit-queue/{fact_id}/preview")
    @serialized
    def audit_preview(document_hash: str, fact_id: str) -> Response:
        index = pdf_index(document_hash)
        matches = [f for f in index.result.facts if pdf_fact_id(f) == fact_id]
        if len(matches) != 1:
            raise HTTPException(status_code=404, detail="Fact revision is missing")
        locator = next((e.locator for e in matches[0].evidence if isinstance(e.locator, PDFLocator)), None)
        if locator is None:
            raise HTTPException(status_code=422, detail="PDF locator is missing")
        return render_audit_page(document_hash, locator.page_number, locator)

    @application.get("/v1/documents/{document_hash}/audit-queue/page/{page_number}")
    @serialized
    def audit_page(document_hash: str, page_number: int) -> Response:
        pdf_index(document_hash)
        return render_audit_page(document_hash, page_number)

    @application.get("/v1/documents/{document_hash}/audit-queue/supplemental/{fact_id}/preview")
    @serialized
    def supplemental_audit_preview(document_hash: str, fact_id: str) -> Response:
        index = pdf_index(document_hash)
        matches = [f for f in index.supplemental_facts if f.fact_id == fact_id]
        if len(matches) != 1 or not isinstance(matches[0].evidence.locator, PDFLocator):
            raise HTTPException(status_code=404, detail="Supplemental revision is missing")
        locator = matches[0].evidence.locator
        return render_audit_page(document_hash, locator.page_number, locator)

    @application.post("/v1/documents/{document_hash}/audit-queue/batch-confirm")
    @serialized
    def batch_confirm(document_hash: str, request: PdfBatchReviewRequest) -> dict:
        index = pdf_index(document_hash)
        group = next((g for g in audit_groups(index) if g["fiscal_year"] == request.fiscal_year
                      and g["form_code"] == request.form_code), None)
        if group is None or not group["batch_eligible"]:
            raise HTTPException(status_code=409, detail="Statement has no eligible zero-residual reconciliation")
        submitted = {r.fact_id: r.confirmed_normalized_value for r in request.rows}
        expected = {r["fact_id"]: Decimal(r["value_vnd"]) for r in group["pending_rows"]
                    if r["value_vnd"] is not None and r["review_bucket"] == "ELIGIBLE_FOR_BATCH"}
        if (len(submitted) != len(request.rows) or any(fact_id not in expected or expected[fact_id] != value
               for fact_id, value in submitted.items())):
            raise HTTPException(status_code=409, detail="Batch must explicitly identify selected pending B01 rows and exact values")
        facts = list(index.result.facts)
        events = []
        for position, fact in enumerate(facts):
            old_id = pdf_fact_id(fact)
            if old_id not in submitted:
                continue
            updated = fact.transition(FactState.VERIFIED, source_checked=True)
            facts[position] = updated
            events.append({"fact_id": old_id, "result_fact_id": pdf_fact_id(updated),
                           "decision": "BATCH_CONFIRM", "reviewer": request.reviewer.strip(),
                           "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
                           "confirmed_normalized_value": str(submitted[old_id]),
                           "fiscal_year": request.fiscal_year, "form_code": request.form_code,
                           "reconciliation_residual_vnd": "0"})
        save_reviewed_pdf(index, facts, events)
        return {"document_hash": document_hash, "signed_count": len(events), "state": "HUMAN_VERIFIED",
                "remaining_pending_count": group["pending_count"] - len(events),
                "reconciliation_residual_vnd": "0"}

    @application.post("/v1/documents/{document_hash}/audit-queue/correct")
    @serialized
    def correct_pdf_fact(document_hash: str, request: PdfCorrectionRequest) -> dict:
        index = pdf_index(document_hash)
        matches = [(i, f) for i, f in enumerate(index.result.facts) if pdf_fact_id(f) == request.fact_id]
        if len(matches) != 1:
            raise HTTPException(status_code=409, detail="Fact is missing, ambiguous or has changed")
        position, fact = matches[0]
        if fact.state not in {FactState.EXTRACTED, FactState.REJECTED_OCR_ERROR}:
            raise HTTPException(status_code=409, detail="Only pending or rejected OCR can be corrected")
        if fact.currency != request.confirmed_currency or fact.period != request.confirmed_period:
            raise HTTPException(status_code=409, detail="Currency or period differs from the source")
        if not any(isinstance(e.locator, PDFLocator) and e.locator.bounding_box for e in fact.evidence):
            raise HTTPException(status_code=409, detail="Correction requires a page and bounding box")
        if (request.corrected_locator is None) != (request.corrected_observed_text is None):
            raise HTTPException(status_code=422, detail="Corrected locator and source text must be supplied together")
        corrected_evidence = fact.evidence
        if request.corrected_locator is not None:
            locator = request.corrected_locator
            if locator.bounding_box is None:
                raise HTTPException(status_code=422, detail="Corrected locator requires a source bounding box")
            old_pages = {e.locator.page_number for e in fact.evidence if isinstance(e.locator, PDFLocator)}
            if locator.page_number not in old_pages:
                raise HTTPException(status_code=422, detail="Correction must stay on the same source page")
            source = upload_dir / f"{document_hash}.pdf"
            try:
                with open(source, "rb") as stream:
                    page = PdfReader(stream).pages[locator.page_number - 1]
                    x0, y0, x1, y1 = locator.bounding_box
                    if x1 > float(page.mediabox.width) or max(y0, y1) > float(page.mediabox.height):
                        raise HTTPException(status_code=422, detail="Corrected box outside source page")
            except (PdfReadError, OSError, IndexError) as error:
                raise HTTPException(status_code=422, detail="Invalid correction source page") from error
            corrected_evidence = (Evidence(document_hash=document_hash, locator=locator,
                                           observed_text=request.corrected_observed_text),)
        updated = FinancialFact.model_validate({**fact.model_dump(),
            "value": str(request.corrected_normalized_value / fact.unit_multiplier),
            "normalized_value": str(request.corrected_normalized_value),
            "zero_from_dash": False,
            "state": FactState.HUMAN_CORRECTED,
            "evidence": [e.model_dump(mode="json") for e in corrected_evidence],
            "verification": VerificationRecord(source_checked=True).model_dump()})
        facts = list(index.result.facts)
        facts[position] = updated
        event = {"fact_id": request.fact_id, "result_fact_id": pdf_fact_id(updated),
                 "decision": "CORRECT", "reviewer": request.reviewer.strip(),
                 "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
                 "original_ocr_value_vnd": str(fact.normalized_value),
                 "corrected_value_vnd": str(request.corrected_normalized_value),
                 "page_bbox_evidence": [e.model_dump(mode="json") for e in fact.evidence],
                 "corrected_page_bbox_evidence": [e.model_dump(mode="json") for e in corrected_evidence],
                 "comment": request.comment}
        save_reviewed_pdf(index, facts, [event])
        return {"document_hash": document_hash, "fact_id": pdf_fact_id(updated),
                "state": updated.state.value, "review": event}

    @application.post("/v1/documents/{document_hash}/audit-queue/propose-missing")
    @serialized
    def propose_missing_pdf_fact(document_hash: str, request: PdfMissingFactRequest) -> dict:
        index = pdf_index(document_hash)
        if request.code not in EXTENDED_TARGET_CODES[request.form_code]:
            raise HTTPException(status_code=422, detail="Unsupported TT200 code")
        if request.locator.bounding_box is None:
            raise HTTPException(status_code=422, detail="Missing fact requires a source bounding box")
        source = upload_dir / f"{document_hash}.pdf"
        try:
            with open(source, "rb") as stream:
                pages = PdfReader(stream).pages
                if request.locator.page_number > len(pages):
                    raise HTTPException(status_code=422, detail="Page outside source PDF")
                page = pages[request.locator.page_number - 1]
                x0, y0, x1, y1 = request.locator.bounding_box
                if x1 > float(page.mediabox.width) or max(y0, y1) > float(page.mediabox.height):
                    raise HTTPException(status_code=422, detail="Bounding box outside source page")
        except (PdfReadError, OSError) as error:
            raise HTTPException(status_code=422, detail="Invalid source PDF") from error
        base = index.result.facts[0].document
        year = int(request.period.fiscal_year_label.removeprefix("FY"))
        if year not in {base.fiscal_period_end.year, base.fiscal_period_end.year - 1}:
            raise HTTPException(status_code=422, detail="Period is outside the source report")
        if request.form_code == "B01-DN":
            expected_date = base.fiscal_period_end if year == base.fiscal_period_end.year else base.fiscal_period_start
            if request.period.period_type != PeriodType.INSTANT or request.period.as_of_date != expected_date:
                raise HTTPException(status_code=422, detail="B01 balance date does not match this report")
        else:
            year_shift = year - base.fiscal_period_end.year
            expected_end = shift_year(base.fiscal_period_end, year_shift)
            expected_start = shift_year(base.fiscal_period_start, year_shift)
            if request.form_code == "B03-DN" and request.code == "70":
                valid_period = request.period.period_type == PeriodType.INSTANT and request.period.as_of_date == expected_end
            else:
                valid_period = (request.period.period_type == PeriodType.DURATION
                                and request.period.period_start == expected_start
                                and request.period.period_end == expected_end)
            if not valid_period:
                raise HTTPException(status_code=422, detail="Period does not match this report")
        if any(f.line_code.form_code == request.form_code and f.line_code.code == request.code
               and f.period == request.period for f in index.result.facts):
            raise HTTPException(status_code=409, detail="Fact for this code and period already exists")
        context = DocumentContext.model_validate({**base.model_dump(), "form_code": request.form_code})
        evidence = Evidence(document_hash=document_hash, locator=request.locator,
                            observed_text=request.observed_text)
        fact = FinancialFact(document=context,
                             line_code=LineItemCode(accounting_regime=context.accounting_regime,
                                                    form_code=request.form_code, code=request.code),
                             original_label=request.original_label,
                             normalized_label=request.original_label.casefold(),
                             period=request.period, raw_value=None,
                             value=request.normalized_value_vnd, currency="VND",
                             unit_label="đồng", unit_multiplier=Decimal(1),
                             normalized_value=request.normalized_value_vnd,
                             evidence=[evidence], state=FactState.HUMAN_CORRECTED,
                             verification=VerificationRecord(source_checked=True))
        event = {"fact_id": None, "result_fact_id": pdf_fact_id(fact),
                 "decision": "CORRECT", "reviewer": request.reviewer.strip(),
                 "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
                 "original_ocr_value_vnd": None,
                 "corrected_value_vnd": str(request.normalized_value_vnd),
                 "page_bbox_evidence": [evidence.model_dump(mode="json")],
                 "comment": request.comment, "origin": "MISSING_OCR_ROW"}
        save_reviewed_pdf(index, [*index.result.facts, fact], [event])
        return {"document_hash": document_hash, "fact_id": pdf_fact_id(fact),
                "state": FactState.HUMAN_CORRECTED.value, "review": event}

    @application.post("/v1/documents/{document_hash}/pdf-note-reviews")
    @serialized
    def review_pdf_note(document_hash: str, request: PdfNoteReviewRequest) -> dict:
        if len(document_hash) != 64 or any(ch not in "0123456789abcdef" for ch in document_hash):
            raise HTTPException(status_code=422, detail="Invalid document hash")
        index = load_index(document_hash)
        if not isinstance(index.result, PdfIngestionResult):
            raise HTTPException(status_code=409, detail="Document is not a PDF index")
        source = upload_dir / f"{document_hash}.pdf"
        if not source.exists() or hashlib.sha256(source.read_bytes()).hexdigest() != document_hash:
            raise HTTPException(status_code=409, detail="Stored document hash mismatch")
        matches = [i for i, fact in enumerate(index.result.facts) if pdf_fact_id(fact) == request.fact_id]
        if len(matches) != 1:
            raise HTTPException(status_code=409, detail="Fact is missing, ambiguous or has changed; reload it")
        position = matches[0]
        fact = index.result.facts[position]
        try:
            if fact.state not in {FactState.VERIFIED, FactState.HUMAN_CORRECTED} or not fact.note_ref or normalize_note_ref(fact.note_ref) != normalize_note_ref(request.note_ref):
                raise HTTPException(status_code=409, detail="Verified fact and matching extracted note reference required")
            if request.page_number > len(PdfReader(source).pages):
                raise HTTPException(status_code=422, detail="Note page is outside the PDF")
        except (ValueError, PdfReadError, OSError) as error:
            raise HTTPException(status_code=422, detail="Invalid note reference or PDF") from error
        evidence = Evidence(document_hash=document_hash, locator=PDFLocator(page_number=request.page_number,
                            table_id="B09-DN", region=f"Thuyết minh {fact.note_ref}"), observed_text=request.excerpt)
        old_note = index.result.notes.get(fact.note_ref)
        if old_note is not None and old_note != evidence:
            raise HTTPException(status_code=409, detail="A different note is already indexed for this reference")
        updated = FinancialFact.model_validate({**fact.model_dump(), "note_link_verified": True,
                                                "note_evidence": evidence.model_dump()})
        event = {"fact_id": request.fact_id, "decision": "CONFIRM_NOTE", "reviewer": request.reviewer.strip(),
                 "reviewed_at_utc": datetime.now(timezone.utc).isoformat(), "note_ref": fact.note_ref,
                 "page_number": request.page_number, "result_fact_id": pdf_fact_id(updated)}
        facts = list(index.result.facts)
        facts[position] = updated
        notes = {**index.result.notes, fact.note_ref: evidence}
        result = PdfIngestionResult(document_hash=document_hash, facts=facts, headers=index.result.headers,
                                    notes=notes, diagnostics=index.result.diagnostics,
                                    review_history=index.result.review_history + (event,))
        updated_index = DocumentIndex.from_ingestion(result,supplemental_facts=index.supplemental_facts)
        staging = upload_dir / f"{document_hash}.index.json.tmp"
        staging.write_text(json.dumps(updated_index.to_json_dict(), ensure_ascii=False), encoding="utf-8")
        staging.replace(upload_dir / f"{document_hash}.index.json")
        application.state.documents[document_hash] = updated_index
        return {"document_hash": document_hash, "fact_id": pdf_fact_id(updated), "review": event}

    @application.post("/v1/qa/query", response_model=QAResponse)
    def query(request: QueryRequest) -> QAResponse:
        return answer_question(load_index(request.document_hash), request.question)

    @application.post("/v1/health-reviews", response_model=HealthReportResponse)
    def health_review(request: HealthReviewRequest) -> HealthReportResponse:
        try:
            return review_health(load_index(request.document_hash), year=request.year)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @application.post("/v1/financial-analysis/full-report", response_model=FullAnalysisReport)
    def full_analysis(request: FullAnalysisRequest) -> FullAnalysisReport:
        try:
            hashes = TenderReviewRequest.validate_hashes(request.document_hashes)
            indexes = [load_index(item) for item in hashes]
            book = AnalysisBook.from_indexes(indexes)
            company = indexes[0].result.facts[0].document.company_name
            return build_full_report(book, company=company, years=request.years)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @application.get("/v1/analysis/documents")
    @serialized
    def analysis_documents() -> dict:
        upload_dir.mkdir(parents=True,exist_ok=True)
        records=[]
        for path in sorted(upload_dir.glob("*.index.json")):
            document_hash=path.name.removesuffix(".index.json")
            if len(document_hash)!=64 or any(ch not in "0123456789abcdef" for ch in document_hash):
                continue
            try:
                index=load_index(document_hash)
                context=index.result.facts[0].document
            except (HTTPException,ValueError,IndexError,KeyError,TypeError):
                continue
            records.append({"document_hash":document_hash,"company_id":context.company_id,
                            "company_name":context.company_name,"report_scope":context.report_scope.value,
                            "fiscal_year":context.fiscal_period_end.year,
                            "source_kind":"pdf" if isinstance(index.result,PdfIngestionResult) else "excel",
                            "fact_count":len(index.result.facts),
                            "verified_facts":sum(f.state==FactState.VERIFIED for f in index.result.facts),
                            "supplemental_count":len(index.supplemental_facts),
                            "verified_supplemental":sum(f.state==FactState.VERIFIED for f in index.supplemental_facts)})
        return {"documents":records}

    @application.get("/v1/documents/{document_hash}/source")
    @serialized
    def indexed_source(document_hash: str):
        if len(document_hash)!=64 or any(ch not in "0123456789abcdef" for ch in document_hash):
            raise HTTPException(status_code=404,detail="Document unavailable")
        index=load_index(document_hash)
        suffix=".pdf" if isinstance(index.result,PdfIngestionResult) else ".xlsx"
        source=upload_dir/f"{document_hash}{suffix}"
        if hashlib.sha256(source.read_bytes()).hexdigest()!=document_hash:
            raise HTTPException(status_code=409,detail="Stored document hash mismatch")
        return FileResponse(source,media_type="application/pdf" if suffix==".pdf" else
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @application.post("/v1/analysis/company-report")
    @serialized
    def live_company_report(request: LiveAnalysisRequest) -> dict:
        try:
            hashes=TenderReviewRequest.validate_hashes(request.document_hashes)
            series=stitch_indexes([load_index(h) for h in hashes],policy=request.restatement_policy,
                                  include_comparative_periods=request.include_comparative_periods)
            company=load_index(hashes[0]).result.facts[0].document.company_name
            report=build_full_report(series.book,company=company,years=request.years)
        except ValueError as error:
            raise HTTPException(status_code=422,detail=str(error)) from error
        supplemental=[item.model_dump(mode="json") for h in hashes
                      for item in load_index(h).supplemental_facts]
        return {"report":report.model_dump(mode="json"),"documents":series.documents,
                "supplemental_facts":supplemental,
                "version_or_restatement_conflicts":series.conflicts,
                "restatement_policy":series.policy,
                "generated_at_utc":datetime.now(timezone.utc).isoformat()}

    @application.post("/v1/analysis/simulations")
    @serialized
    def p10_simulations(request: P10SimulationRequest) -> dict:
        from app.services.analysis_calculator import calculate_metric
        from app.services.analysis_book import weakest, union_evidence
        hashes = TenderReviewRequest.validate_hashes(request.document_hashes)
        try:
            book = stitch_indexes([load_index(h) for h in hashes], policy=request.restatement_policy).book
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        year = request.fiscal_year
        def entry(value, formula, inputs, reason=None):
            present = [d for d in inputs if d is not None]
            return {'status': 'SIMULATION' if value is not None else 'MISSING_INPUT',
                    'value': str(value) if value is not None else None, 'formula': formula,
                    'verification_status': weakest(*(d.verification_status for d in present)),
                    'evidences': [e.model_dump(mode='json') for e in union_evidence(*(d.evidences for d in present))],
                    'reason': reason}
        revenue = [book.get('revenue', z) for z in range(year-request.revenue_years+1, year+1)]
        valid_revenue = all(d is not None and d.value_vnd is not None for d in revenue)
        average = sum((d.value_vnd for d in revenue), Decimal(0))/Decimal(request.revenue_years) if valid_revenue else None
        sales = entry(average*request.duration_years/request.revenue_factor if average is not None else None,
                      'Doanh thu bình quân × thời gian hợp đồng / hệ số doanh thu', revenue,
                      None if valid_revenue else 'Thiếu doanh thu của một hoặc nhiều năm liên tục')
        ca, cl = book.get('current_assets',year), book.get('current_liabilities',year)
        working = ca.value_vnd-cl.value_vnd if ca and cl and ca.value_vnd is not None and cl.value_vnd is not None else None
        liquidity = entry(working/request.financial_resource_ratio if working is not None and working >= 0 else None,
                          'Vốn lưu động ròng / tỷ lệ nguồn lực', [ca,cl],
                          None if working is not None and working >= 0 else 'Thiếu vốn lưu động hoặc vốn lưu động âm')
        cogs = book.get('cogs',year)
        daily = abs(cogs.value_vnd)/Decimal(365) if cogs and cogs.value_vnd is not None else None
        now, previous = calculate_metric(book,'E02',year), calculate_metric(book,'E02',year-1)
        delta = now.value-previous.value if now.value is not None and previous.value is not None else None
        dio_inputs=[book.get('inventory',z) for z in (year-2,year-1,year)]+[book.get('cogs',z) for z in (year-1,year)]
        sensitivity=entry(daily*delta if daily is not None and delta is not None else None,
                          '|Giá vốn năm chọn| / 365 × thay đổi DIO', dio_inputs,
                          None if delta is not None and daily is not None else 'Thiếu số liệu DIO của hai năm')
        sensitivity.update({'per_day': str(daily) if daily is not None else None,
                            'plus_10_days': str(daily*10) if daily is not None else None,
                            'minus_10_days': str(-daily*10) if daily is not None else None,
                            'assumption': 'Giữ nguyên tốc độ giá vốn mỗi ngày; đây là mô phỏng.'})
        return {'kind':'SIMULATION', 'fiscal_year':year, 'revenue_capacity':sales,
                'working_capital_capacity':liquidity, 'dio_sensitivity':sensitivity,
                'description':'Mô phỏng số học theo tham số; không xác nhận điều kiện pháp lý hay kết quả đấu thầu.'}

    @application.post("/v1/analysis/pnj-csv-crosscheck")
    @serialized
    def pnj_csv_crosscheck(request: FullAnalysisRequest) -> dict:
        """Read-only comparison to the owner's P10 CSV; never creates signoffs."""
        from app.services.analysis_book import LINE_CONCEPTS
        approved_sources={
            '7255fd8c26452b56d9bb3f97d2f11bfb0c8556dfa6ab1c63628124bdca2432f8',
            'eab2ae223d1679fc54d6f5b5d05e50f4ebb2e6ece8a18806c3dd0dd33571a437',
        }
        path=Path(__file__).resolve().parents[2]/'docs/pnj_precode/manual_transcription_wide.csv'
        if not path.is_file(): raise HTTPException(status_code=404, detail='CSV đối chiếu không có trong workspace')
        with path.open(encoding='utf-8-sig',newline='') as stream:
            reference={row['metric']:row for row in csv.DictReader(stream)}
        result=[]
        for h in request.document_hashes:
            if h not in approved_sources:continue
            index=load_index(h)
            for fact in index.result.facts:
                doc=fact.document;year=doc.fiscal_period_end.year
                if doc.company_id!='PNJ' or year not in (2024,2025) or fact.period.fiscal_year_label!=f'FY{year}':continue
                concept=LINE_CONCEPTS.get((fact.line_code.form_code,fact.line_code.code))
                raw=reference.get(concept,{}).get(str(year),'')
                if raw=='' or fact.normalized_value is None:continue
                try: expected=Decimal(raw)
                except Exception: continue
                locator=fact.evidence[0].locator if fact.evidence else None
                result.append({'document_hash':h,'fact_id':pdf_fact_id(fact),'year':year,'form':fact.line_code.form_code,
                    'code':fact.line_code.code,'label':fact.original_label,'pdf_value':str(fact.normalized_value),
                    'csv_value':str(expected),'match':'EXACT' if fact.normalized_value==expected else 'DIFFERENT',
                    'state':fact.state.value,'page':getattr(locator,'page_number',None)})
        return {'rows':result,'basis':'Đối chiếu CSV nội bộ; không thay thế việc xác nhận ảnh PDF.'}

    @application.post("/v1/tender-reviews", response_model=TenderReviewResponse)
    def tender_review(request: TenderReviewRequest) -> TenderReviewResponse:
        try:
            hashes = TenderReviewRequest.validate_hashes(request.document_hashes)
            return review_tender([load_index(item) for item in hashes], request.config)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return application


app = create_app()
