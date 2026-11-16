"""Formatting preserves the source while rounding only the visible value."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_compact_money_does_not_lose_original_precision():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the browser formatting regression")
    script = Path(__file__).resolve().parents[1] / "app/web/decimal_format.js"
    program = script.read_text(encoding="utf-8") + "\n" + (
        "console.log(JSON.stringify([formatCompactMoney('37822837171385'),"
        "formatCompactMoney('-12500000'),formatCompactMoney('1.5','vnd'),"
        "formatExactMoney('37822837171385','vnd'),"
        "metricDisplayUnit([{value:null,unit:'%'},{value:'1000000000',unit:'VND'}])]));"
    )
    result = subprocess.run([node, "-e", program], capture_output=True, text=True,
                            encoding="utf-8", check=True)
    assert json.loads(result.stdout) == ["37.822,84 tỷ đồng", "-0,01 tỷ đồng",
                                         "≈ 2 đ", "37.822.837.171.385 đ", "VND"]
