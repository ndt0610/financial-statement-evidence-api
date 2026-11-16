"""M0 fixture and contract checks; these ten questions are not a quality benchmark."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook
from pydantic import ValidationError

from app.models import (
    AnswerResult,
    DocumentContext,
    Evidence,
    ExcelLocator,
    FactState,
    FinancialFact,
    Finding,
    FindingCode,
    LineItemCode,
    Period,
)
from app.services.formula_check import CacheCheck, check_addition_cache


ROOT = Path(__file__).resolve().parents[1]
XLSX = ROOT / "data" / "sample_bctc_2025.xlsx"
GOLD = ROOT / "benchmarks" / "m0_smoke_questions.json"
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@pytest.fixture(scope="module")
def books():
    formula = load_workbook(XLSX, data_only=False)
    values = load_workbook(XLSX, data_only=True)
    yield formula, values
    formula.close()
    values.close()


@pytest.fixture(scope="module")
def gold():
    return json.loads(GOLD.read_text(encoding="utf-8"))


def document(form_code: str = "B01-DN") -> DocumentContext:
    return DocumentContext(
        company_name="Công ty Cổ phần Minh Họa M0",
        company_id="SYNTHETIC-M0",
        report_scope="SEPARATE",
        accounting_regime="TT200",
        form_code=form_code,
        fiscal_year_label="FY2025",
        fiscal_period_start="2025-01-01",
        fiscal_period_end="2025-12-31",
        prepared_on="2025-12-31",
        document_hash=hashlib.sha256(XLSX.read_bytes()).hexdigest(),
        source_path=str(XLSX),
        data_origin="SYNTHETIC",
    )


def make_fact(*, formula: str | None = None, value: str | None = "4000") -> FinancialFact:
    doc = document()
    return FinancialFact(
        document=doc,
        line_code=LineItemCode(accounting_regime="TT200", form_code="B01-DN", code="270"),
        original_label="Tổng cộng tài sản",
        normalized_label="total_assets",
        period=Period(period_type="INSTANT", as_of_date="2025-12-31", fiscal_year_label="FY2025"),
        raw_value=value,
        value=Decimal(value) if value is not None else None,
        currency="VND",
        unit_label="triệu đồng",
        unit_multiplier=Decimal("1000000"),
        normalized_value=Decimal(value) * Decimal("1000000") if value is not None else None,
        evidence=[Evidence(document_hash=doc.document_hash, locator=ExcelLocator(sheet_name="B01-DN", cell_range="D13", formula=formula))],
    )


def test_three_formula_cache_cases_in_xml_and_openpyxl(books):
    formula, values = books
    assert formula.sheetnames == ["B01-DN", "B02-DN", "B09-DN"]
    cases = {
        ("xl/worksheets/sheet1.xml", "D13"): ("D11+D12", "4000"),
        ("xl/worksheets/sheet1.xml", "D16"): ("D14+D15", "3999"),
        ("xl/worksheets/sheet2.xml", "D10"): ("D8+D9", None),
    }
    found = {}
    with ZipFile(XLSX) as archive:
        assert archive.testzip() is None
        for name in ("xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml", "xl/worksheets/sheet3.xml"):
            root = ET.fromstring(archive.read(name))
            for cell in root.findall(".//s:c", NS):
                f = cell.find("s:f", NS)
                if f is not None:
                    v = cell.find("s:v", NS)
                    found[(name, cell.attrib["r"])] = (f.text, None if v is None else v.text)
    assert found == cases
    assert formula["B01-DN"]["D13"].value == "=D11+D12"
    assert formula["B01-DN"]["D16"].value == "=D14+D15"
    assert formula["B02-DN"]["D10"].value == "=D8+D9"
    assert values["B01-DN"]["D13"].value == 4000
    assert values["B01-DN"]["D16"].value == 3999
    assert values["B02-DN"]["D10"].value is None
    assert check_addition_cache(formula["B01-DN"], values["B01-DN"], "D13") == CacheCheck.MATCH
    assert check_addition_cache(formula["B01-DN"], values["B01-DN"], "D16") == CacheCheck.MISMATCH
    assert check_addition_cache(formula["B02-DN"], values["B02-DN"], "D10") == CacheCheck.MISSING_CACHE
    assert check_addition_cache(formula["B01-DN"], values["B01-DN"], "D8") == CacheCheck.UNSUPPORTED


def test_fixture_structure_and_synthetic_label(books):
    formula, values = books
    for ws in formula.worksheets:
        assert "SYNTHETIC" in ws["A1"].value
        assert "KHÔNG PHẢI BCTC THỰC" in ws["A1"].value
        assert ws["A2"].value == "Công ty Cổ phần Minh Họa M0"
        assert ws["D5"].value == "Đơn vị tính: Triệu đồng"
        assert "A1:E1" in [str(item) for item in ws.merged_cells.ranges]
    for sheet in ("B01-DN", "B02-DN"):
        assert [formula[sheet][f"{c}7"].value for c in "ABC"] == ["Chỉ tiêu", "Mã số", "Thuyết minh"]
        assert "RIÊNG" in formula[sheet]["A3"].value
    assert values["B02-DN"]["D11"].value == -3100
    assert "(#,##0)" in formula["B02-DN"]["D11"].number_format


def test_gold_questions_match_fixture_and_answer_contract(books, gold):
    formula, values = books
    assert gold["data_origin"] == "SYNTHETIC"
    assert gold["accounting_regime"] == "TT200"
    questions = gold["questions"]
    assert len(questions) == 10
    assert len({q["id"] for q in questions}) == 10
    assert Counter(q["group"] for q in questions) == {
        "lookup": 2, "negative_unit_period": 2, "ratio_reconciliation": 2,
        "numeric_and_note": 2, "refusal": 2,
    }
    for question in questions:
        expected = question["expected"]
        evidences = []
        for target in expected["evidences"]:
            sheet, cell = target["sheet_name"], target["cell_range"]
            assert sheet in formula.sheetnames
            assert formula[sheet][cell].value is not None
            if "formula" in target:
                assert formula[sheet][cell].value == target["formula"]
            evidences.append(Evidence(document_hash=document().document_hash, locator=ExcelLocator(**target)))
        answer = AnswerResult(
            status=expected["status"],
            explanation=expected.get("reason", question["question"]),
            value=expected["value"],
            unit=expected["unit"],
            period=Period.model_validate(expected["period"]),
            report_scope=expected["report_scope"],
            calculation=expected.get("calculation"),
            evidences=evidences,
        )
        if answer.status == "ANSWERED":
            assert answer.evidences
        else:
            assert answer.value is None

    by_id = {q["id"]: q["expected"] for q in questions}
    for qid in ("Q01", "Q03", "Q04", "Q07", "Q08"):
        assert by_id[qid]["unit"] == values["B01-DN"]["D5"].value.split(": ", 1)[1].lower()
    assert by_id["Q05"]["unit"] == "lần"
    assert values["B01-DN"]["D7"].value == "31/12/2025"
    for qid in ("Q01", "Q04", "Q05", "Q08"):
        assert by_id[qid]["period"]["period_type"] == "INSTANT"
        assert by_id[qid]["period"]["as_of_date"] == "2025-12-31"
    for qid in ("Q02", "Q03", "Q07"):
        assert by_id[qid]["period"] == {
            "period_type": "DURATION", "period_start": "2025-01-01",
            "period_end": "2025-12-31", "fiscal_year_label": "FY2025",
        }
    assert by_id["Q04"]["comparison_period"]["as_of_date"] == "2024-12-31"
    direct_cells = {"Q01": ("B01-DN", "D8"), "Q03": ("B02-DN", "D11"), "Q07": ("B02-DN", "D8"), "Q08": ("B01-DN", "D10")}
    for qid, (sheet, cell) in direct_cells.items():
        assert Decimal(by_id[qid]["value"]) == Decimal(str(values[sheet][cell].value))
    assert Decimal(by_id["Q04"]["value"]) == Decimal(str(values["B01-DN"]["D13"].value)) - Decimal(str(values["B01-DN"]["E13"].value))
    assert Decimal(by_id["Q05"]["value"]) == Decimal(str(values["B01-DN"]["D14"].value)) / Decimal(str(values["B01-DN"]["D15"].value))
    assert by_id["Q02"]["reason"] == "UNVERIFIED_MISSING_CACHE" and values["B02-DN"]["D10"].value is None
    assert by_id["Q06"]["reason"] == "STALE_CACHE_MISMATCH"
    assert by_id["Q09"]["status"] == "REFUSED_SCOPE_MISMATCH" and "RIÊNG" in formula["B01-DN"]["A3"].value
    assert by_id["Q10"]["evidences"] == []
    assert all("2023" not in str(ws["D7"].value) and "2023" not in str(ws["E7"].value) for ws in formula.worksheets)
    for qid, source_sheet, note_cell in (("Q07", "B02-DN", "B8"), ("Q08", "B01-DN", "B9")):
        expected = by_id[qid]
        assert expected["note_ref"] == formula[source_sheet]["C8" if qid == "Q07" else "C10"].value
        assert expected["note_ref"] == formula["B09-DN"]["A8" if qid == "Q07" else "A9"].value
        assert expected["note_excerpt"] == formula["B09-DN"][note_cell].value


def test_decimal_period_namespace_and_state_guards():
    fact = make_fact(formula="=D11+D12")
    assert fact.state == FactState.EXTRACTED
    assert fact.normalized_value == Decimal("4000000000")
    with pytest.raises(ValidationError):
        FinancialFact.model_validate({**fact.model_dump(), "normalized_value": "1"})
    with pytest.raises(ValidationError):
        FinancialFact.model_validate({**fact.model_dump(), "state": "VERIFIED"})
    with pytest.raises(ValidationError):
        FinancialFact.model_validate({**fact.model_dump(), "note_ref": "IV.2", "note_link_verified": True})
    with pytest.raises(ValidationError):
        Period(period_type="INSTANT", as_of_date="2025-12-31", period_start="2025-01-01", fiscal_year_label="FY2025")
    with pytest.raises(ValidationError):
        FinancialFact.model_validate({**fact.model_dump(), "line_code": {"accounting_regime": "TT99", "form_code": "B01-DN", "code": "270"}})
    comparative = FinancialFact.model_validate({
        **fact.model_dump(),
        "period": {"period_type": "INSTANT", "as_of_date": "2024-12-31", "fiscal_year_label": "FY2024"},
    })
    assert comparative.period.fiscal_year_label == "FY2024"
    with pytest.raises(ValueError):
        fact.transition(FactState.VERIFIED, source_checked=True)
    with pytest.raises(ValueError):
        fact.transition(FactState.STALE_CACHE_MISMATCH, source_checked=True, cache_present=True)
    verified = fact.transition(FactState.VERIFIED, source_checked=True, cache_present=True, independent_formula_match=True)
    assert verified.state == FactState.VERIFIED
    stale = fact.transition(FactState.STALE_CACHE_MISMATCH, source_checked=True, cache_present=True, independent_formula_match=False)
    assert stale.state == FactState.STALE_CACHE_MISMATCH
    missing = make_fact(formula="=D11+D12", value=None).transition(FactState.UNVERIFIED_MISSING_CACHE, source_checked=True, cache_present=False)
    assert missing.state == FactState.UNVERIFIED_MISSING_CACHE
    with pytest.raises(ValueError):
        missing.transition(FactState.VERIFIED, source_checked=True, cache_present=True, independent_formula_match=True)
    warning = Finding(code=FindingCode.RECONCILIATION_WARNING, detail="Synthetic mismatch exceeds stated tolerance", tolerance=Decimal("0"), difference=Decimal("1"), evidence=fact.evidence)
    assert verified.with_reconciliation_warning(warning).findings[0].code == FindingCode.RECONCILIATION_WARNING
    with pytest.raises(ValueError):
        stale.with_reconciliation_warning(warning)
    with pytest.raises(ValidationError):
        FinancialFact.model_validate({**stale.model_dump(), "findings": [warning.model_dump()]})


def test_answer_refusal_and_locator_validation():
    doc = document()
    title = Evidence(document_hash=doc.document_hash, locator=ExcelLocator(sheet_name="B01-DN", cell_range="A3"))
    assert AnswerResult(status="REFUSED_SCOPE_MISMATCH", explanation="Only separate statements", evidences=[title]).value is None
    assert AnswerResult(status="REFUSED_MISSING_DATA", explanation="No FY2023", evidences=[]).evidences == []
    with pytest.raises(ValidationError):
        AnswerResult(status="ANSWERED", explanation="No evidence", value=Decimal("1"), unit="triệu đồng", period=Period(period_type="INSTANT", as_of_date="2025-12-31", fiscal_year_label="FY2025"), report_scope="SEPARATE")
    pdf = Evidence(document_hash=doc.document_hash, locator={"source_kind": "pdf", "page_number": 2, "region": "table 1"})
    assert pdf.locator.page_number == 2
    with pytest.raises(ValidationError):
        Evidence(document_hash=doc.document_hash, locator={"source_kind": "pdf", "sheet_name": "2", "cell_range": "A1"})
