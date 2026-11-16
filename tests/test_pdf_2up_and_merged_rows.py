"""Synthetic Docling layouts, with page regions and conservative merged rows."""
from dataclasses import replace

import pytest
from app.adapters.pdf_adapter import _align_misindexed_values, _split_cross_year_cells, _split_vertical_overlap, _split_merged_rows, extract_docling_tables
from app.models import ReportScope
from benchmarks.m4b_real.manual_reference import load_labels, source_spec


def box(l, t, r, b, origin):
    return {"l": l, "t": t, "r": r, "b": b, "coord_origin": origin}


def cell(row, col, text, x=10):
    return {"start_row_offset_idx": row, "start_col_offset_idx": col, "text": text,
            "bbox": box(x, 30, x + 9, 40, "TOPLEFT")}


@pytest.mark.parametrize("origin", ["TOPLEFT", "BOTTOMLEFT"])
@pytest.mark.parametrize("wide", [False, True])
def test_two_up_table_context_and_wide_splitting(origin, wide):
    top, bottom = (20, 90) if origin == "TOPLEFT" else (80, 10)
    title_top, title_bottom = (1, 10) if origin == "TOPLEFT" else (99, 90)
    def table(x, form, code):
        cells = [cell(0, 0, "Mã số", x), cell(0, 1, "Chỉ tiêu", x + 10),
                 cell(0, 2, "2025 (VND)", x + 20), cell(0, 3, "2024 (VND)", x + 30),
                 cell(1, 0, code, x), cell(1, 1, "Chỉ tiêu", x + 10),
                 cell(1, 2, "1.000", x + 20), cell(1, 3, "900", x + 30)]
        return {"self_ref": form, "prov": [{"page_no": 1, "bbox": box(x, top, x + 45, bottom, origin)}],
                "data": {"num_cols": 4, "table_cells": cells}}
    left, right = table(0, "B01", "100"), table(55, "B02", "10")
    tables = [left, right]
    if wide:
        rcells = [{**c, "start_col_offset_idx": c["start_col_offset_idx"] + 4} for c in right["data"]["table_cells"]]
        tables = [{"prov": [{"page_no": 1, "bbox": box(0, top, 100, bottom, origin)}],
                   "data": {"num_cols": 8, "table_cells": left["data"]["table_cells"] + rcells}}]
    texts = [{"text": form + " năm 2025", "prov": [{"page_no": 1, "bbox": box(x, title_top, x + 45, title_bottom, origin)}]}
             for x, form in ((0, "B01-DN/HN"), (55, "B02-DN/HN"))]
    payload = {"status": "SUCCESS", "ocr": True, "document": {"texts": texts, "tables": tables}}
    spec = replace(source_spec(load_labels()["sources"][0]), report_scope=ReportScope.CONSOLIDATED)
    result = extract_docling_tables(payload, spec)
    assert {(f.line_code.form_code, f.line_code.code) for f in result.facts} == {("B01-DN", "100"), ("B02-DN", "10")}
    assert len(result.facts) == 4 and all(f.state.value == "EXTRACTED" for f in result.facts)


def test_merged_missing_token_is_never_assigned():
    cells = [cell(1, 0, "100 110"), cell(1, 1, "one\ntwo"), cell(1, 2, "1 2"), cell(1, 3, "3")]
    assert _split_merged_rows(cells, 0, 2, 3) == cells


def test_spatial_alignment_requires_unique_two_year_matches():
    cells = [cell(11, 0, '270'), cell(11, 1, 'Tổng tài sản'),
             cell(10, 2, '36.362.339.883.577'), cell(12, 3, '34.912.272.846.093')]
    aligned = _align_misindexed_values(cells, 0, 2, 3)
    assert [(x['start_col_offset_idx'], x['text']) for x in aligned if x.get('_spatial_alignment')] == [
        (2, '36.362.339.883.577'), (3, '34.912.272.846.093')]
    assert _align_misindexed_values(cells + [cell(13, 2, '123')], 0, 2, 3) == cells + [cell(13, 2, '123')]
    assert _align_misindexed_values(cells + [cell(14, 0, '280')], 0, 2, 3) == cells + [cell(14, 0, '280')]


def test_cross_year_split_requires_both_columns_and_exactly_two_amounts():
    cells=[cell(0,2,'2024',100),cell(0,3,'2023',200),cell(1,0,'09'),
           {**cell(1,3,'(2.155.159) (3.243.651)'), 'bbox':box(90,30,220,40,'TOPLEFT')}]
    result=_split_cross_year_cells(cells,0,2,3)
    assert [(x['start_col_offset_idx'],x['text']) for x in result if x.get('_cross_year_source')] == [(2,'(2.155.159)'),(3,'(3.243.651)')]
    for raw in ('(2.155.159)', '(2.155.159) (3.243.651) 1.000'):
        altered=[{**x,'text':raw} if x is cells[-1] else x for x in cells]
        assert _split_cross_year_cells(altered,0,2,3)==altered
    altered=[{**x,'bbox':box(190,30,220,40,'TOPLEFT')} if x is cells[-1] else x for x in cells]
    assert _split_cross_year_cells(altered,0,2,3)==altered


def test_vertical_overlap_needs_adjacent_code_and_two_years():
    previous={**cell(5,0,'421b'),'bbox':box(0,20,10,30,'TOPLEFT')}
    current={**cell(6,0,'429'),'bbox':box(0,40,10,50,'TOPLEFT')}
    a={**cell(6,2,'1.000 2.000'),'bbox':box(90,29,110,51,'TOPLEFT')}
    b={**cell(6,3,'3.000 4.000'),'bbox':box(140,29,160,51,'TOPLEFT')}
    cells=[previous,current,a,b];result=_split_vertical_overlap(cells,0,2,3)
    assert [x['text'] for x in result if x.get('_vertical_source')]==['2.000','4.000']
    assert _split_vertical_overlap([current,a,b],0,2,3)==[current,a,b]
    assert _split_vertical_overlap([previous,current,a],0,2,3)==[previous,current,a]
