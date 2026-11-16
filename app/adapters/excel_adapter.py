"""Dual-pass XLSX adapter retaining source cells and conservative M1 states."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.worksheet.worksheet import Worksheet

from app.models import (
    DocumentContext,
    Evidence,
    ExcelLocator,
    FinancialFact,
    Finding,
    LineItemCode,
    Period,
    ReportScope,
)
from app.services.formula_and_reconcile_guard import reconcile_facts, verify_facts
from app.services.vas_normalizer import (
    extract_date,
    fiscal_start,
    fold,
    normalize_label,
    normalize_unit,
    parse_accounting_number,
    period_from_column,
)


FORM_CODES = ("B01-DN", "B02-DN", "B03-DN", "B09-DN")


@dataclass(frozen=True)
class SheetHeader:
    sheet_name: str
    form_code: str
    company_name: str
    company_evidence: Evidence
    report_scope: ReportScope
    scope_evidence: Evidence
    unit_label: str
    unit_multiplier: Decimal
    unit_evidence: Evidence
    fiscal_end: date
    period_columns: dict[int, Period]
    data_start_row: int | None
    label_column: int | None
    code_column: int | None
    note_column: int | None


@dataclass(frozen=True)
class ExcelIngestionResult:
    document_hash: str
    headers: dict[str, SheetHeader]
    facts: list[FinancialFact]
    notes: dict[str, Evidence]

    @property
    def reconciliation_warnings(self) -> list[Finding]:
        return [finding for fact in self.facts for finding in fact.findings]


def _visible_cell(ws: Worksheet, row: int, column: int) -> Cell:
    cell = ws.cell(row, column)
    if cell.value is not None:
        return cell
    for merged in ws.merged_cells.ranges:
        if merged.min_row <= row <= merged.max_row and merged.min_col <= column <= merged.max_col:
            return ws.cell(merged.min_row, merged.min_col)
    return cell


def _header_cells(ws: Worksheet):
    for row in range(1, min(ws.max_row, 25) + 1):
        for column in range(1, min(ws.max_column, 30) + 1):
            cell = ws.cell(row, column)
            if isinstance(cell.value, str) and cell.value.strip():
                yield cell


def _evidence(document_hash: str, ws: Worksheet, cell: Cell, *, formula: str | None = None) -> Evidence:
    return Evidence(
        document_hash=document_hash,
        locator=ExcelLocator(sheet_name=ws.title, cell_range=cell.coordinate, formula=formula),
        observed_text=str(cell.value) if cell.value is not None else None,
    )


def _form_code(ws: Worksheet, title: str) -> str:
    for code in FORM_CODES:
        if code in ws.title.upper():
            return code
    normalized = fold(title)
    if "can doi ke toan" in normalized:
        return "B01-DN"
    if "ket qua hoat dong kinh doanh" in normalized:
        return "B02-DN"
    if "luu chuyen tien te" in normalized:
        return "B03-DN"
    if "thuyet minh" in normalized:
        return "B09-DN"
    raise ValueError(f"cannot identify TT200 form on sheet {ws.title!r}")


def _is_supported_sheet(ws: Worksheet) -> bool:
    if any(code in ws.title.upper() for code in FORM_CODES):
        return True
    return any(
        cell.row <= 6 and any(phrase in fold(cell.value) for phrase in (
            "can doi ke toan", "ket qua hoat dong kinh doanh", "luu chuyen tien te", "thuyet minh bao cao tai chinh",
        ))
        for cell in _header_cells(ws)
    )


def _detect_table_columns(ws: Worksheet) -> tuple[int, int, int, int]:
    for row in range(1, min(ws.max_row, 30) + 1):
        labels: dict[str, int] = {}
        for column in range(1, ws.max_column + 1):
            value = _visible_cell(ws, row, column).value
            if not isinstance(value, str):
                continue
            label = " ".join(fold(value).split())
            if label in {"chi tieu", "tai san"}:
                labels["label"] = column
            elif label == "ma so":
                labels["code"] = column
            elif label == "thuyet minh":
                labels["note"] = column
        if {"label", "code", "note"} <= labels.keys():
            return row, labels["label"], labels["code"], labels["note"]
    raise ValueError(f"cannot find line-code/note table header on {ws.title!r}")


def detect_header(ws: Worksheet, document_hash: str) -> SheetHeader:
    """Locate table metadata by text, including anchors of merged header cells."""
    cells = list(_header_cells(ws))
    company = next((cell for cell in cells if fold(cell.value).strip().startswith("cong ty ")), None)
    title = next((cell for cell in cells if any(key in fold(cell.value) for key in ("can doi ke toan", "ket qua hoat dong kinh doanh", "luu chuyen tien te", "thuyet minh bao cao tai chinh"))), None)
    unit = next((cell for cell in cells if "don vi tinh" in fold(cell.value)), None)
    dated = next((cell for cell in cells if ("tai ngay" in fold(cell.value) or "ket thuc ngay" in fold(cell.value)) and extract_date(cell.value)), None)
    if company is None or title is None or unit is None or dated is None:
        raise ValueError(f"incomplete company/title/unit/fiscal header on {ws.title!r}")
    title_text = fold(title.value)
    if "hop nhat" in title_text:
        scope = ReportScope.CONSOLIDATED
    elif "rieng" in title_text:
        scope = ReportScope.SEPARATE
    else:
        raise ValueError(f"report scope absent on {ws.title!r}")
    form_code = _form_code(ws, title.value)
    unit_label, multiplier = normalize_unit(unit.value)
    fiscal_end = extract_date(dated.value)
    if form_code == "B09-DN":
        columns = {}
        data_start = label_col = code_col = note_col = None
    else:
        header_row, label_col, code_col, note_col = _detect_table_columns(ws)
        columns: dict[int, Period] = {}
        last_period_row = header_row
        for column in range(note_col + 1, ws.max_column + 1):
            for row in range(header_row, min(header_row + 3, ws.max_row) + 1):
                value = _visible_cell(ws, row, column).value
                if not isinstance(value, str):
                    continue
                period = period_from_column(value, form_code, fiscal_end)
                if period is not None:
                    columns[column] = period
                    last_period_row = max(last_period_row, row)
            # The last recognized heading in a merged/stacked header wins.
        if not columns:
            raise ValueError(f"period columns absent on {ws.title!r}")
        data_start = last_period_row + 1
    return SheetHeader(
        sheet_name=ws.title, form_code=form_code, company_name=company.value.strip(),
        company_evidence=_evidence(document_hash, ws, company),
        report_scope=scope, scope_evidence=_evidence(document_hash, ws, title),
        unit_label=unit_label, unit_multiplier=multiplier,
        unit_evidence=_evidence(document_hash, ws, unit),
        fiscal_end=fiscal_end, period_columns=columns,
        data_start_row=data_start, label_column=label_col,
        code_column=code_col, note_column=note_col,
    )


def _note_index(ws: Worksheet | None, document_hash: str) -> dict[str, Evidence]:
    if ws is None:
        return {}
    notes = {}
    for row in range(1, ws.max_row + 1):
        reference, description = ws.cell(row, 1), ws.cell(row, 2)
        if isinstance(reference.value, str) and re.fullmatch(r"[IVX]+\.\d+", reference.value.strip()) and isinstance(description.value, str) and description.value.strip():
            notes[reference.value.strip()] = _evidence(document_hash, ws, description)
    return notes


def ingest_excel(
    path: str | Path,
    *,
    accounting_regime: str | None = None,
    company_id: str = "UNAVAILABLE",
    data_origin: Literal["SYNTHETIC", "REAL"] | None = None,
    prepared_on: date | None = None,
    reconciliation_tolerance_vnd: Decimal = Decimal("0"),
) -> ExcelIngestionResult:
    """Extract B01/B02/B03 amount cells and verify only supported formulas.

    `accounting_regime` is caller metadata, not inferred from sheet codes. The
    marked synthetic FY2025 fixture alone defaults to TT200; other inputs must
    declare their regime and origin explicitly.
    """
    path = Path(path).resolve()
    if path.suffix.lower() != ".xlsx":
        raise ValueError("M1 adapter accepts .xlsx only")
    document_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    formulas = load_workbook(path, data_only=False, read_only=False)
    cached = load_workbook(path, data_only=True, read_only=False)
    try:
        if formulas.sheetnames != cached.sheetnames:
            raise ValueError("dual-pass sheet mismatch")
        synthetic_marker = any("SYNTHETIC" in str(ws["A1"].value).upper() for ws in formulas.worksheets)
        if synthetic_marker and data_origin == "REAL":
            raise ValueError("synthetic workbook cannot be declared REAL")
        if data_origin is None:
            if not synthetic_marker:
                raise ValueError("declare data_origin for an unmarked workbook")
            data_origin = "SYNTHETIC"
        headers = {ws.title: detect_header(ws, document_hash) for ws in formulas.worksheets if _is_supported_sheet(ws)}
        if not headers:
            raise ValueError("no supported B01/B02/B03/B09 sheet")
        if accounting_regime is None:
            if data_origin != "SYNTHETIC" or {header.fiscal_end.year for header in headers.values()} != {2025}:
                raise ValueError("declare accounting_regime for this workbook")
            accounting_regime = "TT200"
        company_names = {header.company_name for header in headers.values()}
        if len(company_names) != 1:
            raise ValueError("company header mismatch between sheets")
        note_sheet = next((formulas[name] for name, header in headers.items() if header.form_code == "B09-DN"), None)
        notes = _note_index(note_sheet, document_hash)
        extracted: list[FinancialFact] = []
        for sheet_name, header in headers.items():
            if header.form_code not in {"B01-DN", "B02-DN", "B03-DN"}:
                continue
            formula_ws, cache_ws = formulas[sheet_name], cached[sheet_name]
            document = DocumentContext(
                company_name=header.company_name, company_id=company_id,
                report_scope=header.report_scope, accounting_regime=accounting_regime,
                form_code=header.form_code, fiscal_year_label=f"FY{header.fiscal_end.year}",
                fiscal_period_start=fiscal_start(header.fiscal_end), fiscal_period_end=header.fiscal_end,
                prepared_on=prepared_on, document_hash=document_hash,
                source_path=str(path), data_origin=data_origin,
            )
            for row in range(header.data_start_row, formula_ws.max_row + 1):
                label = formula_ws.cell(row, header.label_column).value
                code = formula_ws.cell(row, header.code_column).value
                if not isinstance(label, str) or code is None or not str(code).strip():
                    continue
                note_value = formula_ws.cell(row, header.note_column).value
                note_ref = str(note_value).strip() if note_value is not None else None
                note_evidence = notes.get(note_ref) if note_ref else None
                for column, period in header.period_columns.items():
                    source = formula_ws.cell(row, column)
                    cache = cache_ws.cell(row, column)
                    if source.value is None and cache.value is None:
                        continue
                    formula = source.value if source.data_type == "f" else None
                    raw_value = None if cache.value is None else str(cache.value)
                    value = parse_accounting_number(cache.value)
                    extracted.append(FinancialFact(
                        document=document,
                        line_code=LineItemCode(accounting_regime=accounting_regime, form_code=header.form_code, code=str(code).strip()),
                        original_label=label.strip(), normalized_label=normalize_label(label),
                        note_ref=note_ref, note_link_verified=note_evidence is not None,
                        note_evidence=note_evidence, period=period, raw_value=raw_value,
                        value=value, currency="VND", unit_label=header.unit_label,
                        unit_multiplier=header.unit_multiplier,
                        normalized_value=value * header.unit_multiplier if value is not None else None,
                        evidence=[_evidence(document_hash, formula_ws, source, formula=formula)],
                    ))
        verified = verify_facts(extracted)
        return ExcelIngestionResult(
            document_hash=document_hash,
            headers=headers,
            facts=reconcile_facts(verified, tolerance_vnd=reconciliation_tolerance_vnd),
            notes=notes,
        )
    finally:
        formulas.close()
        cached.close()
