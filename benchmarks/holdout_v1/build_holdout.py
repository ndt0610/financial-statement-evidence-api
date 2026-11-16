"""Build three independent synthetic FY2025 TT200 XLSX benchmark documents.

These are invented companies and values. They are deliberately separate from
the M0 smoke workbook and are not examples of real financial disclosures.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font


HERE = Path(__file__).resolve().parent
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
ET.register_namespace("", NS)
STAMP = (2025, 12, 31, 0, 0, 0)
SYNTHETIC = "SYNTHETIC — DỮ LIỆU TỔNG HỢP, KHÔNG PHẢI BCTC THỰC"


def _header(ws, *, company: str, title: str, unit: str, complex_layout: bool) -> int:
    for row, value in ((1, SYNTHETIC), (2, company), (3, title), (4, "Tại ngày 31 tháng 12 năm 2025" if ws.title == "B01-DN" else "Cho năm tài chính kết thúc ngày 31 tháng 12 năm 2025")):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
        ws.cell(row, 1, value)
        ws.cell(row, 1).alignment = Alignment(horizontal="center")
        ws.cell(row, 1).font = Font(bold=True)
    unit_row = 6 if complex_layout else 5
    ws.merge_cells(start_row=unit_row, start_column=4, end_row=unit_row, end_column=5)
    ws.cell(unit_row, 4, f"Đơn vị tính: {unit}")
    ws.column_dimensions["A"].width = 51
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 17
    ws.column_dimensions["D"].width = 24
    ws.column_dimensions["E"].width = 24
    header_row = 9 if complex_layout else 7
    if ws.title != "B09-DN":
        ws.cell(header_row, 1, "TÀI SẢN" if complex_layout and ws.title == "B01-DN" else "Chỉ tiêu")
        ws.cell(header_row, 2, "Mã số")
        ws.cell(header_row, 3, "Thuyết minh")
        if complex_layout:
            ws.merge_cells(start_row=header_row, start_column=4, end_row=header_row + 1, end_column=4)
            ws.merge_cells(start_row=header_row, start_column=5, end_row=header_row + 1, end_column=5)
            headings = ("Số cuối năm", "Số đầu năm") if ws.title == "B01-DN" else ("Năm nay", "Năm trước")
        else:
            headings = ("31/12/2025", "31/12/2024") if ws.title == "B01-DN" else ("Năm 2025", "Năm 2024")
        ws.cell(header_row, 4, headings[0])
        ws.cell(header_row, 5, headings[1])
        ws.freeze_panes = f"D{header_row + (2 if complex_layout else 1)}"
    return header_row + (2 if complex_layout else 1)


def _rows(ws, start: int, rows: list[tuple[str, str, str | None, object, object]]) -> None:
    for offset, (label, code, note, current, prior) in enumerate(rows):
        row = start + offset
        for column, value in zip("ABCDE", (label, code, note, current, prior)):
            ws[f"{column}{row}"] = value
        for column in "DE":
            ws[f"{column}{row}"].number_format = '#,##0;(#,##0);–'


def _notes(ws, start: int, notes: list[tuple[str, str]]) -> None:
    ws.cell(start - 1, 1, "Mục")
    ws.cell(start - 1, 2, "Nội dung thuyết minh")
    ws.column_dimensions["B"].width = 95
    for offset, (reference, text) in enumerate(notes):
        ws.cell(start + offset, 1, reference)
        ws.cell(start + offset, 2, text)


def _workbook(kind: str) -> Workbook:
    wb = Workbook()
    wb.properties.created = datetime(2025, 12, 31)
    wb.properties.modified = datetime(2025, 12, 31)
    balance = wb.active
    balance.title = "B01-DN"
    income = wb.create_sheet("B02-DN")
    notes = wb.create_sheet("B09-DN")
    if kind == "standard":
        company, scope, unit, complex_layout = "Công ty Cổ phần Sao Bắc (tổng hợp)", "RIÊNG", "Triệu đồng", False
        balance_rows = [
            ("Tiền và các khoản tương đương tiền", "110", "IV.3", 1800, 1300),
            ("Hàng tồn kho", "140", "IV.2", 700, 600),
            ("Tài sản ngắn hạn", "100", None, 5000, 4000),
            ("Tài sản dài hạn", "200", None, 4000, 3000),
            ("Tổng cộng tài sản", "270", None, 9000, 7000),
            ("Nợ phải trả", "300", "IV.4", 5400, 4600),
            ("Vốn chủ sở hữu", "400", None, 3600, 2400),
            ("Tổng cộng nguồn vốn", "440", None, 9000, 7000),
        ]
        income_rows = [
            ("Doanh thu bán hàng và cung cấp dịch vụ", "01", "IV.1", 12500, 10500),
            ("Các khoản giảm trừ doanh thu", "02", None, -500, -500),
            ("Doanh thu thuần", "10", "IV.1", 12000, 10000),
            ("Giá vốn hàng bán", "11", None, -7200, -6000),
            ("Lợi nhuận gộp", "20", None, 4800, 4000),
            ("Lợi nhuận sau thuế", "60", None, 1200, 1000),
        ]
        note_rows = [
            ("IV.1", "Doanh thu của Sao Bắc đến từ bán thiết bị mô phỏng; đây là dữ liệu tổng hợp."),
            ("IV.2", "Hàng tồn kho Sao Bắc gồm linh kiện và thành phẩm mô phỏng."),
            ("IV.3", "Tiền Sao Bắc bao gồm tiền gửi thanh toán mô phỏng."),
            ("IV.4", "Nợ phải trả Sao Bắc chủ yếu là khoản vay mô phỏng."),
        ]
    elif kind == "complex":
        company, scope, unit, complex_layout = "Công ty Cổ phần Đại Dương Giả Lập", "HỢP NHẤT", "Đồng", True
        t = 1_000_000_000_000
        balance_rows = [
            ("Tiền và tương đương tiền", "110", None, 2*t, 1*t),
            ("Hàng tồn kho", "140", "VI.02", 1_500_000_000_000, 1*t),
            ("Tổng cộng tài sản", "270", "V.03", 10*t, 6*t),
            ("Nợ và nghĩa vụ phải trả", "300", None, 6*t, 4*t),
            ("Vốn thuộc chủ sở hữu", "400", None, 4*t, 2*t),
            ("Tổng cộng nguồn vốn", "440", None, 10*t, 6*t),
        ]
        income_rows = [
            ("Doanh thu bán hàng và dịch vụ", "01", "V.01", 13*t, 11*t),
            ("Các khoản giảm trừ doanh thu", "02", None, f"({1*t})", f"({1*t})"),
            ("Doanh thu thuần", "10", None, 12*t, 10*t),
            ("Giá vốn hàng bán", "11", "VI.04", "(7200000000000)", "(6000000000000)"),
            ("Lợi nhuận gộp", "20", None, 4_800_000_000_000, 4*t),
            ("Lợi nhuận sau thuế", "60", None, 1_200_000_000_000, 1*t),
        ]
        note_rows = [
            ("V.01", "Doanh thu Đại Dương đến từ dịch vụ logistics mô phỏng."),
            ("VI.02", "Hàng tồn kho Đại Dương gồm vật tư và hàng đang vận chuyển mô phỏng."),
            ("V.03", "Tài sản Đại Dương gồm tài sản vận tải mô phỏng."),
            ("VI.04", "Giá vốn Đại Dương bao gồm chi phí vận hành mô phỏng."),
        ]
    elif kind == "dirty":
        company, scope, unit, complex_layout = "Công ty Cổ phần Kiểm Toán Thử (tổng hợp)", "RIÊNG", "Nghìn đồng", False
        balance_rows = [
            ("Tiền và các khoản tương đương tiền", "110", "III.3", 800000, 700000),
            ("Hàng tồn kho", "140", "III.2", 350000, 300000),
            ("Các khoản phải thu ngắn hạn", "130", None, 450000, 400000),
            ("Tài sản ngắn hạn", "100", None, 3000000, 2400000),
            ("Tài sản dài hạn", "200", None, 2000000, 1600000),
            ("Tổng cộng tài sản", "270", None, "=D11+D12", 4000000),
            ("Nợ phải trả", "300", None, 3000000, 2400000),
            ("Vốn chủ sở hữu", "400", None, 1999000, 1600000),
            ("Tổng cộng nguồn vốn", "440", None, "=D14+D15", 4000000),
        ]
        income_rows = [
            ("Doanh thu bán hàng và cung cấp dịch vụ", "01", "III.1", 8000000, 7000000),
            ("Các khoản giảm trừ doanh thu", "02", None, -200000, -150000),
            ("Doanh thu thuần", "10", None, "=D8+D9", 6850000),
            ("Giá vốn hàng bán", "11", None, -4500000, -4000000),
            ("Lợi nhuận gộp", "20", None, 3300000, 2850000),
            ("Lợi nhuận sau thuế", "60", "III.4", 450000, 400000),
        ]
        note_rows = [
            ("III.1", "Doanh thu Kiểm Toán Thử đến từ hợp đồng mô phỏng."),
            ("III.2", "Hàng tồn kho Kiểm Toán Thử gồm vật liệu mô phỏng."),
            ("III.3", "Tiền Kiểm Toán Thử là số dư ngân hàng mô phỏng."),
            ("III.4", "Lợi nhuận Kiểm Toán Thử có khoản thu nhập bất thường mô phỏng."),
        ]
    else:
        raise ValueError(kind)
    b_start = _header(balance, company=company, title=f"BẢNG CÂN ĐỐI KẾ TOÁN {scope}", unit=unit, complex_layout=complex_layout)
    i_start = _header(income, company=company, title=f"BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH {scope}", unit=unit, complex_layout=complex_layout)
    n_start = _header(notes, company=company, title=f"BẢN THUYẾT MINH BÁO CÁO TÀI CHÍNH {scope}", unit=unit, complex_layout=complex_layout)
    _rows(balance, b_start, balance_rows)
    _rows(income, i_start, income_rows)
    _notes(notes, n_start, note_rows)
    return wb


def _patch_cache(body: bytes, cell: str, cached: str | None) -> bytes:
    root = ET.fromstring(body)
    target = root.find(f".//{{{NS}}}c[@r='{cell}']")
    if target is None or target.find(f"{{{NS}}}f") is None:
        raise ValueError(f"formula absent: {cell}")
    node = target.find(f"{{{NS}}}v")
    if cached is None:
        if node is not None:
            target.remove(node)
    else:
        if node is None:
            node = ET.SubElement(target, f"{{{NS}}}v")
        node.text = cached
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def build_file(kind: str, destination: Path) -> Path:
    book = _workbook(kind)
    with NamedTemporaryFile(suffix=".xlsx", dir=destination.parent, delete=False) as temporary:
        raw_path = Path(temporary.name)
    try:
        book.save(raw_path)
        with ZipFile(raw_path) as source, ZipFile(destination, "w", ZIP_DEFLATED) as target:
            for member in source.infolist():
                body = source.read(member.filename)
                if member.filename == "docProps/core.xml":
                    body = re.sub(
                        rb"(<dcterms:modified[^>]*>).*?(</dcterms:modified>)",
                        rb"\g<1>2025-12-31T00:00:00Z\2",
                        body,
                    )
                if kind == "dirty" and member.filename == "xl/worksheets/sheet1.xml":
                    body = _patch_cache(body, "D13", "5000000")
                    body = _patch_cache(body, "D16", "4998000")
                elif kind == "dirty" and member.filename == "xl/worksheets/sheet2.xml":
                    body = _patch_cache(body, "D10", None)
                member.date_time = STAMP
                target.writestr(member, body)
    finally:
        raw_path.unlink(missing_ok=True)
    formula_book = load_workbook(destination, data_only=False, read_only=True)
    cached_book = load_workbook(destination, data_only=True, read_only=True)
    try:
        if kind == "dirty":
            assert formula_book["B01-DN"]["D13"].value == "=D11+D12"
            assert cached_book["B01-DN"]["D13"].value == 5000000
            assert cached_book["B01-DN"]["D16"].value == 4998000
            assert cached_book["B02-DN"]["D10"].value is None
    finally:
        formula_book.close()
        cached_book.close()
    return destination


def build_all() -> dict:
    HERE.mkdir(parents=True, exist_ok=True)
    settings = (
        ("standard", "standard_tt200.xlsx", "SEPARATE", "triệu đồng", "0"),
        ("complex", "complex_unit_trap.xlsx", "CONSOLIDATED", "đồng", "0"),
        ("dirty", "audit_trap.xlsx", "SEPARATE", "nghìn đồng", "500000"),
    )
    files = []
    for kind, filename, scope, unit, tolerance in settings:
        path = build_file(kind, HERE / filename)
        files.append({
            "id": kind, "path": filename, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "accounting_regime": "TT200", "data_origin": "SYNTHETIC",
            "report_scope": scope, "unit": unit,
            "reconciliation_tolerance_vnd": tolerance,
        })
    manifest = {"name": "holdout_v1", "purpose": "independent synthetic benchmark, not real-world/CV evidence", "files": files}
    (HERE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    result = build_all()
    print(json.dumps({entry["id"]: entry["sha256"] for entry in result["files"]}, ensure_ascii=True))
