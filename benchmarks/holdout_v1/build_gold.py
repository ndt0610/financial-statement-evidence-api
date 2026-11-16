"""Curated, auditable labels for the three synthetic holdout workbooks.

Values and cells below are declared independently of the product's router and
calculator. The output JSON is the frozen scoring input for holdout_v1.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
FILES = {
    "standard": ("SEPARATE", "triệu đồng"),
    "complex": ("CONSOLIDATED", "đồng"),
    "dirty": ("SEPARATE", "nghìn đồng"),
}
FORMULAS = {
    ("dirty", "B01-DN!D13"): "=D11+D12",
    ("dirty", "B01-DN!D16"): "=D14+D15",
    ("dirty", "B02-DN!D10"): "=D8+D9",
}
questions: list[dict] = []


def add(
    id: str,
    file_id: str,
    group: str,
    question: str,
    *,
    form: str,
    year: int,
    cells: tuple[str, ...] = (),
    value: str | int | None = None,
    unit: str | None = None,
    status: str = "ANSWERED",
    scope: str | None = None,
    verification: str | None = None,
    note_excerpt: str | None = None,
    trap_type: str | None = None,
    metric: str | None = None,
    basis: str | None = None,
    finding_codes: tuple[str, ...] = (),
    as_of_date: str | None = None,
) -> None:
    default_scope, default_unit = FILES[file_id]
    if form == "B01-DN":
        period = {"period_type": "INSTANT", "as_of_date": as_of_date or f"{year}-12-31", "fiscal_year_label": f"FY{year}"}
    else:
        period = {"period_type": "DURATION", "period_start": f"{year}-01-01", "period_end": f"{year}-12-31", "fiscal_year_label": f"FY{year}"}
    evidences = []
    for cell in cells:
        sheet, address = cell.split("!")
        item = {"sheet_name": sheet, "cell_range": address}
        if formula := FORMULAS.get((file_id, cell)):
            item["formula"] = formula
        evidences.append(item)
    expected = {
        "status": status, "value": str(value) if value is not None else None,
        "unit": (unit or default_unit) if status == "ANSWERED" else None,
        "period": period, "report_scope": scope or default_scope,
        "verification_status": verification or ("VERIFIED" if status == "ANSWERED" else "NOT_APPLICABLE"),
        "evidences": evidences,
    }
    if note_excerpt is not None:
        expected["note_excerpt"] = note_excerpt
    if trap_type is not None:
        expected["trap_type"] = trap_type
    if metric is not None:
        expected["metric"] = metric
    if basis is not None:
        expected["basis"] = basis
    if finding_codes:
        expected["finding_codes"] = list(finding_codes)
    questions.append({"id": id, "file_id": file_id, "group": group, "question": question, "expected": expected})


L, U, M, H, R = "lookup", "unit_negative", "metric_reconciliation", "numeric_note", "refusal_trap"

# Standard separate statement: 4 questions in each of the five groups.
add("S-L1", "standard", L, "Tiền và các khoản tương đương tiền cuối 2025 là bao nhiêu?", form="B01-DN", year=2025, value=1800, cells=("B01-DN!D8",))
add("S-L2", "standard", L, "Hàng tồn kho cuối 2024 là bao nhiêu?", form="B01-DN", year=2024, value=600, cells=("B01-DN!E9",))
add("S-L3", "standard", L, "Doanh thu bán hàng năm 2025 là bao nhiêu?", form="B02-DN", year=2025, value=12500, cells=("B02-DN!D8",))
add("S-L4", "standard", L, "Lợi nhuận sau thuế năm 2025 là bao nhiêu?", form="B02-DN", year=2025, value=1200, cells=("B02-DN!D13",))
add("S-U1", "standard", U, "Các khoản giảm trừ doanh thu năm 2025 ghi âm bao nhiêu triệu đồng?", form="B02-DN", year=2025, value=-500, cells=("B02-DN!D9",))
add("S-U2", "standard", U, "Giá vốn hàng bán năm 2025 là số âm bao nhiêu?", form="B02-DN", year=2025, value=-7200, cells=("B02-DN!D11",))
add("S-U3", "standard", U, "Doanh thu thuần năm 2024 là bao nhiêu triệu đồng?", form="B02-DN", year=2024, value=10000, cells=("B02-DN!E10",))
add("S-U4", "standard", U, "Vốn chủ sở hữu cuối 2024 là bao nhiêu triệu đồng?", form="B01-DN", year=2024, value=2400, cells=("B01-DN!E14",))
add("S-M1", "standard", M, "Hệ số nợ phải trả trên vốn chủ sở hữu cuối 2025 của Sao Bắc là bao nhiêu lần?", form="B01-DN", year=2025, value="1.5", unit="lần", metric="DEBT_TO_EQUITY", cells=("B01-DN!D13", "B01-DN!D14"))
add("S-M2", "standard", M, "Biên lợi nhuận gộp (Gross Margin) năm 2025 là bao nhiêu %?", form="B02-DN", year=2025, value=40, unit="%", metric="GROSS_MARGIN", cells=("B02-DN!D12", "B02-DN!D10"))
add("S-M3", "standard", M, "Biên lợi nhuận ròng (Net Margin) năm 2025 là bao nhiêu %?", form="B02-DN", year=2025, value=10, unit="%", metric="NET_MARGIN", cells=("B02-DN!D13", "B02-DN!D10"))
add("S-M4", "standard", M, "ROA bình quân năm 2025 là bao nhiêu %?", form="B02-DN", year=2025, value=15, unit="%", metric="ROA", basis="average", cells=("B02-DN!D13", "B01-DN!D12", "B01-DN!E12"))
add("S-H1", "standard", H, "Doanh thu bán hàng năm 2025 là bao nhiêu và thuyết minh IV.1 nói gì?", form="B02-DN", year=2025, value=12500, cells=("B02-DN!D8", "B09-DN!B8"), note_excerpt="Doanh thu của Sao Bắc đến từ bán thiết bị mô phỏng; đây là dữ liệu tổng hợp.")
add("S-H2", "standard", H, "Hàng tồn kho cuối 2025 là bao nhiêu, thuyết minh IV.2 mô tả gì?", form="B01-DN", year=2025, value=700, cells=("B01-DN!D9", "B09-DN!B9"), note_excerpt="Hàng tồn kho Sao Bắc gồm linh kiện và thành phẩm mô phỏng.")
add("S-H3", "standard", H, "Tiền và các khoản tương đương tiền cuối 2025 là bao nhiêu, thuyết minh IV.3 nói gì?", form="B01-DN", year=2025, value=1800, cells=("B01-DN!D8", "B09-DN!B10"), note_excerpt="Tiền Sao Bắc bao gồm tiền gửi thanh toán mô phỏng.")
add("S-H4", "standard", H, "Nợ phải trả cuối 2025 là bao nhiêu, thuyết minh IV.4 nói gì?", form="B01-DN", year=2025, value=5400, cells=("B01-DN!D13", "B09-DN!B11"), note_excerpt="Nợ phải trả Sao Bắc chủ yếu là khoản vay mô phỏng.")
add("S-R1", "standard", R, "Trên báo cáo hợp nhất, tổng tài sản cuối 2025 của Sao Bắc là bao nhiêu?", form="B01-DN", year=2025, status="REFUSED_SCOPE_MISMATCH", scope="CONSOLIDATED", trap_type="scope", cells=("B01-DN!A3",))
add("S-R2", "standard", R, "Cho biết doanh thu thuần năm tài chính 2023 của Sao Bắc?", form="B02-DN", year=2023, status="REFUSED_MISSING_DATA", trap_type="missing_period")
add("S-R3", "standard", R, "Hàng tồn kho cuối 2022 là bao nhiêu?", form="B01-DN", year=2022, status="REFUSED_MISSING_DATA", trap_type="missing_period")
add("S-R4", "standard", R, "Doanh thu bán hàng hợp nhất năm 2024 là bao nhiêu?", form="B02-DN", year=2024, status="REFUSED_SCOPE_MISMATCH", scope="CONSOLIDATED", trap_type="scope", cells=("B02-DN!A3",))

# Consolidated statement with merged period headings and very large VND values.
T = 1_000_000_000_000
add("C-L1", "complex", L, "Tiền và tương đương tiền hợp nhất cuối 2025 là bao nhiêu đồng?", form="B01-DN", year=2025, value=2*T, cells=("B01-DN!D11",))
add("C-L2", "complex", L, "Hàng tồn kho hợp nhất đầu năm 2025 là bao nhiêu đồng?", form="B01-DN", year=2024, as_of_date="2025-01-01", value=1*T, cells=("B01-DN!E12",))
add("C-L3", "complex", L, "Doanh thu thuần hợp nhất năm 2025 là bao nhiêu đồng?", form="B02-DN", year=2025, value=12*T, cells=("B02-DN!D13",))
add("C-L4", "complex", L, "Vốn thuộc chủ sở hữu hợp nhất cuối 2025 là bao nhiêu đồng?", form="B01-DN", year=2025, value=4*T, cells=("B01-DN!D15",))
add("C-U1", "complex", U, "Các khoản giảm trừ doanh thu hợp nhất năm 2025 ghi âm bao nhiêu đồng?", form="B02-DN", year=2025, value=-1*T, cells=("B02-DN!D12",))
add("C-U2", "complex", U, "Giá vốn hàng bán hợp nhất năm 2025 là số âm bao nhiêu đồng?", form="B02-DN", year=2025, value=-7_200_000_000_000, cells=("B02-DN!D14",))
add("C-U3", "complex", U, "Tiền và tương đương tiền hợp nhất cuối 2025 là bao nhiêu, giữ nguyên đơn vị đồng?", form="B01-DN", year=2025, value=2*T, cells=("B01-DN!D11",))
add("C-U4", "complex", U, "Lợi nhuận gộp hợp nhất năm 2025 là bao nhiêu đồng?", form="B02-DN", year=2025, value=4_800_000_000_000, cells=("B02-DN!D15",))
add("C-M1", "complex", M, "Tỷ số nợ phải trả trên vốn chủ sở hữu hợp nhất cuối 2025 là bao nhiêu lần?", form="B01-DN", year=2025, value="1.5", unit="lần", metric="DEBT_TO_EQUITY", cells=("B01-DN!D14", "B01-DN!D15"))
add("C-M2", "complex", M, "Gross Margin hợp nhất năm 2025 là bao nhiêu %?", form="B02-DN", year=2025, value=40, unit="%", metric="GROSS_MARGIN", cells=("B02-DN!D15", "B02-DN!D13"))
add("C-M3", "complex", M, "Doanh thu thuần hợp nhất tăng trưởng YoY 2025 so với 2024 bao nhiêu %?", form="B02-DN", year=2025, value=20, unit="%", metric="GROWTH_YOY", cells=("B02-DN!D13", "B02-DN!E13"))
add("C-M4", "complex", M, "ROE bình quân hợp nhất năm 2025 là bao nhiêu %?", form="B02-DN", year=2025, value=40, unit="%", metric="ROE", basis="average", cells=("B02-DN!D16", "B01-DN!D15", "B01-DN!E15"))
add("C-H1", "complex", H, "Doanh thu bán hàng hợp nhất năm 2025 là bao nhiêu và thuyết minh V.01 nói gì?", form="B02-DN", year=2025, value=13*T, cells=("B02-DN!D11", "B09-DN!B11"), note_excerpt="Doanh thu Đại Dương đến từ dịch vụ logistics mô phỏng.")
add("C-H2", "complex", H, "Hàng tồn kho hợp nhất cuối 2025 là bao nhiêu, thuyết minh VI.02 mô tả gì?", form="B01-DN", year=2025, value=1_500_000_000_000, cells=("B01-DN!D12", "B09-DN!B12"), note_excerpt="Hàng tồn kho Đại Dương gồm vật tư và hàng đang vận chuyển mô phỏng.")
add("C-H3", "complex", H, "Tổng tài sản hợp nhất cuối 2025 là bao nhiêu, thuyết minh V.03 nói gì?", form="B01-DN", year=2025, value=10*T, cells=("B01-DN!D13", "B09-DN!B13"), note_excerpt="Tài sản Đại Dương gồm tài sản vận tải mô phỏng.")
add("C-H4", "complex", H, "Giá vốn hàng bán hợp nhất năm 2025 là bao nhiêu, thuyết minh VI.04 nói gì?", form="B02-DN", year=2025, value=-7_200_000_000_000, cells=("B02-DN!D14", "B09-DN!B14"), note_excerpt="Giá vốn Đại Dương bao gồm chi phí vận hành mô phỏng.")
add("C-R1", "complex", R, "Tổng tài sản riêng cuối 2025 là bao nhiêu?", form="B01-DN", year=2025, status="REFUSED_SCOPE_MISMATCH", scope="SEPARATE", trap_type="scope", cells=("B01-DN!A3",))
add("C-R2", "complex", R, "Doanh thu thuần hợp nhất năm 2023 là bao nhiêu?", form="B02-DN", year=2023, status="REFUSED_MISSING_DATA", trap_type="missing_period")
add("C-R3", "complex", R, "Hàng tồn kho riêng đầu năm 2025 là bao nhiêu?", form="B01-DN", year=2024, as_of_date="2025-01-01", status="REFUSED_SCOPE_MISMATCH", scope="SEPARATE", trap_type="scope", cells=("B01-DN!A3",))
add("C-R4", "complex", R, "Tỷ số nợ phải trả trên vốn chủ sở hữu hợp nhất cuối 2022 là bao nhiêu?", form="B01-DN", year=2022, status="REFUSED_MISSING_DATA", trap_type="missing_period")

# Dirty statement: one missing cache, one stale cache, and a 1m VND imbalance.
add("D-L1", "dirty", L, "Tiền và các khoản tương đương tiền cuối 2025 là bao nhiêu nghìn đồng?", form="B01-DN", year=2025, value=800000, cells=("B01-DN!D8",))
add("D-L2", "dirty", L, "Tổng tài sản cuối 2025 là bao nhiêu nghìn đồng?", form="B01-DN", year=2025, value=5000000, cells=("B01-DN!D13",), finding_codes=("RECONCILIATION_WARNING",))
add("D-L3", "dirty", L, "Doanh thu bán hàng năm 2025 là bao nhiêu nghìn đồng?", form="B02-DN", year=2025, value=8000000, cells=("B02-DN!D8",))
add("D-L4", "dirty", L, "Lợi nhuận sau thuế năm 2024 là bao nhiêu nghìn đồng?", form="B02-DN", year=2024, value=400000, cells=("B02-DN!E13",))
add("D-U1", "dirty", U, "Các khoản giảm trừ doanh thu năm 2025 ghi âm bao nhiêu nghìn đồng?", form="B02-DN", year=2025, value=-200000, cells=("B02-DN!D9",))
add("D-U2", "dirty", U, "Giá vốn hàng bán năm 2025 là số âm bao nhiêu nghìn đồng?", form="B02-DN", year=2025, value=-4500000, cells=("B02-DN!D11",))
add("D-U3", "dirty", U, "Tiền và các khoản tương đương tiền cuối 2024 là bao nhiêu nghìn đồng?", form="B01-DN", year=2024, value=700000, cells=("B01-DN!E8",))
add("D-U4", "dirty", U, "Vốn chủ sở hữu cuối 2025 là bao nhiêu nghìn đồng?", form="B01-DN", year=2025, value=1999000, cells=("B01-DN!D15",))
add("D-M1", "dirty", M, "Tỷ số nợ phải trả trên vốn chủ sở hữu cuối 2024 là bao nhiêu lần?", form="B01-DN", year=2024, value="1.5", unit="lần", metric="DEBT_TO_EQUITY", cells=("B01-DN!E14", "B01-DN!E15"))
add("D-M2", "dirty", M, "ROA bình quân năm 2025 là bao nhiêu %?", form="B02-DN", year=2025, value=10, unit="%", metric="ROA", basis="average", cells=("B02-DN!D13", "B01-DN!D13", "B01-DN!E13"))
add("D-M3", "dirty", M, "Đối chiếu tổng tài sản với nợ phải trả và vốn chủ sở hữu cuối 2025: có cân đối không?", form="B01-DN", year=2025, status="REFUSED_MISSING_DATA", verification="UNVERIFIED", trap_type="reconciliation", cells=("B01-DN!D13", "B01-DN!D14", "B01-DN!D15"), finding_codes=("RECONCILIATION_WARNING",))
add("D-M4", "dirty", M, "Lợi nhuận sau thuế tăng trưởng YoY năm 2025 so với 2024 bao nhiêu %?", form="B02-DN", year=2025, value="12.5", unit="%", metric="GROWTH_YOY", cells=("B02-DN!D13", "B02-DN!E13"))
add("D-H1", "dirty", H, "Doanh thu bán hàng năm 2025 là bao nhiêu và thuyết minh III.1 nói gì?", form="B02-DN", year=2025, value=8000000, cells=("B02-DN!D8", "B09-DN!B8"), note_excerpt="Doanh thu Kiểm Toán Thử đến từ hợp đồng mô phỏng.")
add("D-H2", "dirty", H, "Hàng tồn kho cuối 2025 là bao nhiêu, thuyết minh III.2 mô tả gì?", form="B01-DN", year=2025, value=350000, cells=("B01-DN!D9", "B09-DN!B9"), note_excerpt="Hàng tồn kho Kiểm Toán Thử gồm vật liệu mô phỏng.")
add("D-H3", "dirty", H, "Tiền và các khoản tương đương tiền cuối 2025 là bao nhiêu, thuyết minh III.3 nói gì?", form="B01-DN", year=2025, value=800000, cells=("B01-DN!D8", "B09-DN!B10"), note_excerpt="Tiền Kiểm Toán Thử là số dư ngân hàng mô phỏng.")
add("D-H4", "dirty", H, "Lợi nhuận sau thuế năm 2025 là bao nhiêu, thuyết minh III.4 nói gì?", form="B02-DN", year=2025, value=450000, cells=("B02-DN!D13", "B09-DN!B11"), note_excerpt="Lợi nhuận Kiểm Toán Thử có khoản thu nhập bất thường mô phỏng.")
add("D-R1", "dirty", R, "Doanh thu thuần năm 2025 là bao nhiêu khi cache công thức trống?", form="B02-DN", year=2025, status="REFUSED_MISSING_DATA", verification="UNVERIFIED_MISSING_CACHE", trap_type="missing_cache", cells=("B02-DN!D10",))
add("D-R2", "dirty", R, "Tổng nguồn vốn cuối 2025 là bao nhiêu khi cache đã cũ?", form="B01-DN", year=2025, status="REFUSED_MISSING_DATA", verification="STALE_CACHE_MISMATCH", trap_type="stale_cache", cells=("B01-DN!D16",))
add("D-R3", "dirty", R, "Tổng nguồn vốn cuối 2025 có đối chiếu được không khi cache công thức sai?", form="B01-DN", year=2025, status="REFUSED_MISSING_DATA", verification="STALE_CACHE_MISMATCH", trap_type="stale_cache", cells=("B01-DN!D16", "B01-DN!D14", "B01-DN!D15"))
add("D-R4", "dirty", R, "Với báo cáo hợp nhất, tổng tài sản cuối 2025 của Kiểm Toán Thử là bao nhiêu?", form="B01-DN", year=2025, status="REFUSED_SCOPE_MISMATCH", scope="CONSOLIDATED", trap_type="scope", cells=("B01-DN!A3",))


def build_gold() -> dict:
    counts = Counter(item["group"] for item in questions)
    assert len(questions) == 60
    assert len({item["id"] for item in questions}) == 60
    assert counts == {L: 12, U: 12, M: 12, H: 12, R: 12}
    assert Counter(item["file_id"] for item in questions) == {key: 20 for key in FILES}
    data = {
        "name": "holdout_v1",
        "origin": "SYNTHETIC",
        "warning": "Independent of M0 smoke questions; authored with knowledge of M2 and not a real-filing benchmark",
        "questions": questions,
    }
    (HERE / "gold.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


if __name__ == "__main__":
    print(len(build_gold()["questions"]))
