"""Versioned dependency catalogue for the existing 52-metric calculator."""
import json
from functools import lru_cache
from pathlib import Path

from app.services.analysis_calculator import FORMULA_BY_ID, METRIC_IDS


@lru_cache(maxsize=1)
def metric_matrix():
    rows = json.loads(Path(__file__).with_name("metric_matrix.json").read_text(encoding="utf-8"))
    if {row["id"] for row in rows} != set(METRIC_IDS) or len(rows) != len(METRIC_IDS):
        raise ValueError("Metric matrix must cover each of the 52 indicators exactly once")
    if any(row["formula"] != FORMULA_BY_ID[row["id"]] for row in rows):
        raise ValueError("Metric matrix formula metadata is out of date")
    return rows
