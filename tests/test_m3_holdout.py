"""Audit the independent synthetic holdout before measuring any system."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook

from app.adapters import ingest_excel


ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "benchmarks" / "holdout_v1"
MANIFEST = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
GOLD = json.loads((HERE / "gold.json").read_text(encoding="utf-8"))


def _number(value) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, str) and value.startswith("(") and value.endswith(")"):
        return -Decimal(value[1:-1])
    return Decimal(str(value))


def test_manifest_is_three_distinct_synthetic_rebuildable_files():
    assert len(MANIFEST["files"]) == 3
    assert {item["id"] for item in MANIFEST["files"]} == {"standard", "complex", "dirty"}
    assert len({item["sha256"] for item in MANIFEST["files"]}) == 3
    companies = set()
    for item in MANIFEST["files"]:
        path = HERE / item["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
        assert item["data_origin"] == "SYNTHETIC" and item["accounting_regime"] == "TT200"
        wb = load_workbook(path, data_only=False)
        try:
            assert wb.sheetnames == ["B01-DN", "B02-DN", "B09-DN"]
            assert all("SYNTHETIC" in ws["A1"].value for ws in wb.worksheets)
            companies.add(wb["B01-DN"]["A2"].value)
            if item["id"] == "complex":
                assert "D9:D10" in [str(span) for span in wb["B01-DN"].merged_cells.ranges]
                assert wb["B02-DN"]["D12"].value.startswith("(")
        finally:
            wb.close()
    assert len(companies) == 3


def test_dirty_cache_and_rounding_traps_are_real():
    path = HERE / "audit_trap.xlsx"
    formula = load_workbook(path, data_only=False)
    values = load_workbook(path, data_only=True)
    try:
        assert formula["B01-DN"]["D13"].value == "=D11+D12"
        assert values["B01-DN"]["D13"].value == 5000000
        assert formula["B01-DN"]["D16"].value == "=D14+D15"
        assert values["B01-DN"]["D16"].value == 4998000
        assert values["B01-DN"]["D14"].value + values["B01-DN"]["D15"].value == 4999000
        assert formula["B02-DN"]["D10"].value == "=D8+D9"
        assert values["B02-DN"]["D10"].value is None
        assert values["B01-DN"]["D13"].value - (values["B01-DN"]["D14"].value + values["B01-DN"]["D15"].value) == 1000
        assert 1000 * 1000 > int(next(item for item in MANIFEST["files"] if item["id"] == "dirty")["reconciliation_tolerance_vnd"])
    finally:
        formula.close()
        values.close()
    ingested = ingest_excel(path, accounting_regime="TT200", data_origin="SYNTHETIC", reconciliation_tolerance_vnd=Decimal("500000"))
    assert len(ingested.reconciliation_warnings) == 1


def test_gold_is_balanced_and_disjoint_from_m0_smoke():
    questions = GOLD["questions"]
    smoke = json.loads((ROOT / "benchmarks" / "m0_smoke_questions.json").read_text(encoding="utf-8"))
    assert len(questions) == 60
    assert len({item["id"] for item in questions}) == 60
    assert Counter(item["group"] for item in questions) == {
        "lookup": 12, "unit_negative": 12, "metric_reconciliation": 12,
        "numeric_note": 12, "refusal_trap": 12,
    }
    assert Counter(item["file_id"] for item in questions) == {"standard": 20, "complex": 20, "dirty": 20}
    assert not {item["question"] for item in questions} & {item["question"] for item in smoke["questions"]}


def test_benchmark_report_counts_and_failure_list_are_consistent():
    report = json.loads((ROOT / "benchmarks" / "results_v1.json").read_text(encoding="utf-8"))
    assert report["question_count"] == 60
    assert len(report["per_question"]) == 60
    assert {item["id"] for item in report["per_question"]} == {item["id"] for item in GOLD["questions"]}
    expected_failures = {item["id"] for item in report["per_question"] if item["evidence_failure_reasons"]}
    assert expected_failures == {item["id"] for item in report["failure_cases"]}
    for metrics in report["metrics"].values():
        assert metrics["answerable_count"] == 47
        assert metrics["trap_count"] == 13
        assert metrics["latency_samples"] == 180
        assert 0 <= metrics["numeric_exact_match_percent"] <= 100
        assert 0 <= metrics["evidence_cell_hit_rate_percent"] <= 100
        assert metrics["numeric_exact_match_percent"] == round(metrics["numeric_correct"] / metrics["answerable_count"] * 100, 2)
        assert metrics["evidence_cell_hit_rate_percent"] == round(metrics["evidence_cells_hit"] / metrics["evidence_cells_expected"] * 100, 2)


@pytest.mark.parametrize("file_id", ["standard", "complex", "dirty"])
def test_all_gold_values_and_evidence_against_workbook(file_id):
    item = next(entry for entry in MANIFEST["files"] if entry["id"] == file_id)
    formula = load_workbook(HERE / item["path"], data_only=False)
    values = load_workbook(HERE / item["path"], data_only=True)
    try:
        for question in (entry for entry in GOLD["questions"] if entry["file_id"] == file_id):
            expected = question["expected"]
            for evidence in expected["evidences"]:
                sheet, cell = evidence["sheet_name"], evidence["cell_range"]
                assert formula[sheet][cell].value is not None, question["id"]
                if "formula" in evidence:
                    assert formula[sheet][cell].value == evidence["formula"], question["id"]
            if expected["status"] != "ANSWERED":
                assert expected["value"] is None and expected["unit"] is None
                continue
            assert expected["report_scope"] == item["report_scope"]
            assert expected["unit"] in {item["unit"], "%", "lần"}
            cells = expected["evidences"]
            if "metric" not in expected:
                observed = _number(values[cells[0]["sheet_name"]][cells[0]["cell_range"]].value)
                assert observed == Decimal(expected["value"]), question["id"]
            else:
                numbers = [_number(values[cell["sheet_name"]][cell["cell_range"]].value) for cell in cells]
                metric = expected["metric"]
                if metric == "DEBT_TO_EQUITY":
                    observed = numbers[0] / numbers[1]
                elif metric in {"GROSS_MARGIN", "NET_MARGIN"}:
                    observed = numbers[0] / numbers[1] * 100
                elif metric in {"ROA", "ROE"}:
                    observed = numbers[0] / ((numbers[1] + numbers[2]) / 2) * 100
                elif metric == "GROWTH_YOY":
                    observed = (numbers[0] - numbers[1]) / numbers[1] * 100
                else:
                    raise AssertionError(metric)
                assert observed == Decimal(expected["value"]), question["id"]
            if "note_excerpt" in expected:
                note = cells[-1]
                assert values[note["sheet_name"]][note["cell_range"]].value == expected["note_excerpt"]
    finally:
        formula.close()
        values.close()
