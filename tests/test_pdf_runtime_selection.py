"""Keep the web PDF path aligned with the Docling environment used by the project."""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from app.api import main


def test_pdf_converter_prefers_bundled_docling_python(tmp_path, monkeypatch):
    monkeypatch.delenv("M4B_DOCLING_PYTHON", raising=False)
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        Path(command[5]).write_text(json.dumps({"status": "SUCCESS", "errors": []}), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    result = main.run_pdf_converter(tmp_path / "input.pdf", tmp_path / "out.json", 7, 11, True)
    bundled = Path(main.__file__).resolve().parents[2] / ".venv-m4b" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )

    assert result["status"] == "SUCCESS"
    assert commands[0][0] == str(bundled if bundled.is_file() else sys.executable)
    assert commands[0][-1] == "--ocr"
