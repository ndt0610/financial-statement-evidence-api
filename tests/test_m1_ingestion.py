"""M1 extraction, period normalization, cache guards and reconciliation."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from app.adapters import ingest_excel
from app.models import FactState, FinancialFact, FindingCode, PeriodType, ReportScope
from app.services.formula_and_reconcile_guard import calculate_formula, reconcile_facts, verify_facts
from app.services.vas_normalizer import normalize_unit, parse_accounting_number, period_from_column


ROOT = Path(__file__).resolve().parents[1]
XLSX = ROOT / "data" / "sample_bctc_2025.xlsx"
EXPECTED_ROWS = {
    "B01-DN": {8: ("110", None), 9: ("130", None), 10: ("140", "IV.2"),
               11: ("100", None), 12: ("200", None), 13: ("270", None),
               14: ("300", None), 15: ("400", None), 16: ("440", None)},
    "B02-DN": {8: ("01", "IV.1"), 9: ("02", None), 10: ("10", None),
               11: ("11", None), 12: ("20", None), 13: ("25", None),
               14: ("26", None), 15: ("30", None)},
}


@pytest.fixture(scope="module")
def result():
    return ingest_excel(XLSX)


def fact_at(result, sheet: str, address: str) -> FinancialFact:
    return next(fact for fact in result.facts if fact.evidence[0].locator.sheet_name == sheet and fact.evidence[0].locator.cell_range == address)


def test_all_fixture_amount_cells_extracted_with_exact_provenance(result):
    expected_coords = {(sheet, f"{col}{row}") for sheet, rows in EXPECTED_ROWS.items() for row in rows for col in "DE"}
    extracted_coords = {(fact.evidence[0].locator.sheet_name, fact.evidence[0].locator.cell_range) for fact in result.facts}
    assert len(result.facts) == len(expected_coords) == 34
    assert extracted_coords == expected_coords  # 34/34 cell and period records
    assert result.document_hash == hashlib.sha256(XLSX.read_bytes()).hexdigest()
    formula_book = load_workbook(XLSX, data_only=False)
    cache_book = load_workbook(XLSX, data_only=True)
    try:
        for sheet, rows in EXPECTED_ROWS.items():
            for row, (code, note) in rows.items():
                for col in "DE":
                    fact = fact_at(result, sheet, f"{col}{row}")
                    original = formula_book[sheet][f"{col}{row}"]
                    cached = cache_book[sheet][f"{col}{row}"].value
                    assert fact.line_code.code == code
                    assert fact.line_code.form_code == sheet
                    assert fact.line_code.accounting_regime == "TT200"
                    assert fact.original_label == formula_book[sheet][f"A{row}"].value
                    assert fact.note_ref == note
                    assert fact.evidence[0].locator.formula == (original.value if original.data_type == "f" else None)
                    assert fact.value == parse_accounting_number(cached)
                    assert fact.unit_label == "triệu đồng"
                    assert fact.unit_multiplier == Decimal("1000000")
                    assert fact.normalized_value == (fact.value * Decimal("1000000") if fact.value is not None else None)
                    assert fact.document.document_hash == result.document_hash
                    assert fact.document.report_scope == ReportScope.SEPARATE
                    assert fact.document.data_origin == "SYNTHETIC"
                    assert fact.period.period_type == (PeriodType.INSTANT if sheet == "B01-DN" else PeriodType.DURATION)
                    assert fact.period.fiscal_year_label == ("FY2025" if col == "D" else "FY2024")
        assert fact_at(result, "B01-DN", "D10").note_link_verified
        assert fact_at(result, "B01-DN", "D10").note_evidence.locator.cell_range == "B9"
        assert fact_at(result, "B02-DN", "D8").note_evidence.locator.cell_range == "B8"
    finally:
        formula_book.close()
        cache_book.close()


def test_header_evidence_and_three_cache_cases(result):
    assert len(result.headers) == 3
    for header in result.headers.values():
        assert header.company_name == "Công ty Cổ phần Minh Họa M0"
        assert header.company_evidence.locator.cell_range == "A2"
        assert header.scope_evidence.locator.cell_range == "A3"
        assert header.unit_evidence.locator.cell_range == "D5"
        assert header.report_scope == ReportScope.SEPARATE
        assert header.unit_multiplier == Decimal("1000000")
        assert header.fiscal_end == date(2025, 12, 31)
    valid = fact_at(result, "B01-DN", "D13")
    missing = fact_at(result, "B02-DN", "D10")
    stale = fact_at(result, "B01-DN", "D16")
    assert (valid.state, valid.value, valid.calculated_value) == (FactState.VERIFIED, Decimal("4000"), Decimal("4000"))
    assert (missing.state, missing.value, missing.calculated_value) == (FactState.UNVERIFIED_MISSING_CACHE, None, Decimal("5000"))
    assert (stale.state, stale.value, stale.calculated_value) == (FactState.STALE_CACHE_MISMATCH, Decimal("3999"), Decimal("4000"))
    assert Counter(fact.state for fact in result.facts) == {
        FactState.VERIFIED: 32,
        FactState.UNVERIFIED_MISSING_CACHE: 1,
        FactState.STALE_CACHE_MISMATCH: 1,
    }
    assert result.reconciliation_warnings == []  # stale cached 440 cannot be used to claim imbalance


@pytest.mark.parametrize("raw,expected", [
    ("(125.500)", Decimal("-125500")),
    ("1.234.567,89", Decimal("1234567.89")),
    ("1,234,567.89", Decimal("1234567.89")),
    ("2,5", Decimal("2.5")),
    ("-", None),
    ("", None),
    (-3100, Decimal("-3100")),
])
def test_accounting_number_normalization(raw, expected):
    assert parse_accounting_number(raw) == expected


@pytest.mark.parametrize("unit,multiplier", [
    ("đồng", "1"), ("nghìn đồng", "1000"),
    ("Triệu đồng", "1000000"), ("tỷ đồng", "1000000000"),
])
def test_unit_normalization(unit, multiplier):
    assert normalize_unit(unit)[1] == Decimal(multiplier)


def test_sum_chain_and_unsupported_formula(result):
    index = {(f.evidence[0].locator.sheet_name, f.evidence[0].locator.cell_range): f for f in result.facts}
    assert calculate_formula("=SUM(D8:D10)", "B01-DN", index) == Decimal("2500")
    assert calculate_formula("=E10+E11-E12", "B01-DN", index) == Decimal("1050")
    assert calculate_formula("=AVERAGE(D8:D10)", "B01-DN", index) is None
    source = fact_at(result, "B01-DN", "D13")
    payload = source.model_dump()
    payload["evidence"][0]["locator"]["formula"] = "=AVERAGE(D11:D12)"
    payload["state"] = "EXTRACTED"
    payload["verification"] = None
    payload["calculated_value"] = None
    unsupported = FinancialFact.model_validate(payload)
    assert verify_facts([unsupported])[0].state == FactState.EXTRACTED


def test_reconciliation_warning_only_over_tolerance(result):
    debt = fact_at(result, "B01-DN", "D14")
    modified = FinancialFact.model_validate({**debt.model_dump(), "value": "2401", "normalized_value": "2401000000"})
    facts = [modified if item is debt else item for item in result.facts]
    warned = reconcile_facts(facts, tolerance_vnd=Decimal("500000"))
    asset = next(item for item in warned if item.line_code.code == "270" and item.period.as_of_date == date(2025, 12, 31))
    assert len(asset.findings) == 1
    assert asset.findings[0].code == FindingCode.RECONCILIATION_WARNING
    assert asset.findings[0].difference == Decimal("1000000")
    assert asset.findings[0].tolerance == Decimal("500000")
    allowed = reconcile_facts(facts, tolerance_vnd=Decimal("1000000"))
    assert not next(item for item in allowed if item.line_code.code == "270" and item.period.as_of_date == date(2025, 12, 31)).findings
    source = fact_at(result, "B01-DN", "E16")
    wrong_source = FinancialFact.model_validate({**source.model_dump(), "value": "3499", "normalized_value": "3499000000"})
    previous_facts = [wrong_source if item is source else item for item in result.facts]
    previous = reconcile_facts(previous_facts, tolerance_vnd=Decimal("0"))
    previous_asset = next(item for item in previous if item.line_code.code == "270" and item.period.as_of_date == date(2024, 12, 31))
    assert len(previous_asset.findings) == 1
    assert "440" in previous_asset.findings[0].detail


def test_merged_relative_period_headers(tmp_path):
    book = Workbook()
    for sheet_name, title, headings in (
        ("B01-DN", "BẢNG CÂN ĐỐI KẾ TOÁN RIÊNG", ("Số cuối năm", "Số đầu năm")),
        ("B02-DN", "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH RIÊNG", ("Năm nay", "Năm trước")),
    ):
        ws = book.active if sheet_name == "B01-DN" else book.create_sheet()
        ws.title = sheet_name
        for row, text in ((1, "SYNTHETIC — KHÔNG PHẢI BCTC THỰC"), (2, "Công ty Mẫu"), (3, title), (4, "Cho năm tài chính kết thúc ngày 31 tháng 12 năm 2025")):
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
            ws.cell(row, 1, text)
        ws.merge_cells("D5:E5")
        ws["D5"] = "Đơn vị tính: nghìn đồng" if sheet_name == "B02-DN" else "Đơn vị tính: tỷ đồng"
        ws["A7"], ws["B7"], ws["C7"] = "Chỉ tiêu", "Mã số", "Thuyết minh"
        ws.merge_cells("D7:D8")
        ws.merge_cells("E7:E8")
        ws["D7"], ws["E7"] = headings
        ws["A9"], ws["B9"], ws["D9"], ws["E9"] = "Mục thử", "270" if sheet_name == "B01-DN" else "01", 10, 9
    path = tmp_path / "merged.xlsx"
    book.save(path)
    ingested = ingest_excel(path)
    assert len(ingested.facts) == 4
    assert fact_at(ingested, "B01-DN", "D9").period.as_of_date == date(2025, 12, 31)
    assert fact_at(ingested, "B01-DN", "E9").period.as_of_date == date(2025, 1, 1)
    assert fact_at(ingested, "B01-DN", "E9").period.fiscal_year_label == "FY2024"
    assert fact_at(ingested, "B02-DN", "D9").period.period_start == date(2025, 1, 1)
    assert fact_at(ingested, "B02-DN", "E9").period.period_end == date(2024, 12, 31)
    assert fact_at(ingested, "B01-DN", "D9").unit_multiplier == Decimal("1000000000")
    assert fact_at(ingested, "B02-DN", "D9").unit_multiplier == Decimal("1000")
    assert period_from_column("Năm nay", "B02-DN", date(2025, 12, 31)).period_type == PeriodType.DURATION
    book["B01-DN"].title = "CanDoi"
    book["B02-DN"].title = "KetQua"
    book.save(path)
    renamed = ingest_excel(path)
    assert len(renamed.facts) == 4
    assert fact_at(renamed, "CanDoi", "D9").line_code.form_code == "B01-DN"
    for ws in book.worksheets:
        ws["A3"] = ws["A3"].value.replace("RIÊNG", "HỢP NHẤT")
    book.save(path)
    consolidated = ingest_excel(path)
    assert all(fact.document.report_scope == ReportScope.CONSOLIDATED for fact in consolidated.facts)
    assert all(header.scope_evidence.locator.cell_range == "A3" for header in consolidated.headers.values())
