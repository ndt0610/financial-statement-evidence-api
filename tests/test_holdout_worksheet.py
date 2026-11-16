"""Check that draft questions cannot masquerade as independently reviewed labels."""
from collections import Counter
import hashlib
import io

import pytest
from pypdf import PdfWriter

from scripts.build_holdout_manifest import build
from scripts.check_review_worksheet import validate
from scripts.prepare_holdout_questions import questions


def candidates():
    return {"documents": [{"id": f"doc{i}", "scope": "CONSOLIDATED", "fiscal_year": 2025 - i}
                          for i in range(3)]}


def test_generated_questions_have_no_answers_or_review_claims():
    rows = questions(candidates())
    assert len(rows) == 60
    assert set(Counter(row["group"] for row in rows).values()) == {12}
    assert len({row["case_id"] for row in rows}) == 60
    assert all(not row["normalized_value"] and not row["independent_reviewer"] and row["review_status"] == "UNLABELED" for row in rows)
    result = validate(rows, {f"doc{i}" for i in range(3)})
    assert not result["ready"] and len(result["issues"]) == 60


def test_reviewer_declaration_and_required_evidence_cannot_be_omitted():
    rows = questions(candidates())
    for row in rows:
        row.update(review_status="APPROVED", preparer="Alice", independent_reviewer="Bob")
        if row["group"] == "TRAP":
            row.update(expected_disposition="REFUSE", reason="unavailable")
        else:
            row.update(expected_disposition="ANSWER", normalized_value="12", unit="VND", period="FY2025",
                       scope="CONSOLIDATED", source_observation="source",
                       evidence_regions_json='[{"page":1,"bbox":["1","2","3","4"]},{"page":2,"bbox":["1","2","3","4"]}]',
                       independent_calculation="10 + 2")
    assert validate(rows, {f"doc{i}" for i in range(3)})["ready"]
    rows[0]["independent_reviewer"] = "alice"
    assert not validate(rows, {f"doc{i}" for i in range(3)})["ready"]
    rows[0]["independent_reviewer"] = "Bob"
    rows[0]["evidence_regions_json"] = "[]"
    assert not validate(rows, {f"doc{i}" for i in range(3)})["ready"]


def test_manifest_builder_refuses_unreviewed_and_produces_only_draft(tmp_path):
    sources = candidates()
    sources["status"] = "DEVELOPMENT_ONLY_NOT_ELIGIBLE_FOR_HOLDOUT"
    with pytest.raises(ValueError, match="development documents"):
        build([], sources, {"documents": []}, tmp_path)
    del sources["status"]
    profiles = {"documents": []}
    for i, doc in enumerate(sources["documents"]):
        path = tmp_path / f"{doc['id']}.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=100 + i, height=100)
        writer.add_blank_page(width=100 + i, height=100)
        buffer = io.BytesIO()
        writer.write(buffer)
        path.write_bytes(buffer.getvalue())
        doc.update(company_id=f"company{i}", source_url="https://example.org/", local_path=path.name,
                   sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        profiles["documents"].append({"id": doc["id"], "status": "REVIEWED", "reviewer": "Metadata reviewer",
                                     "scope": "CONSOLIDATED", "fiscal_year": doc["fiscal_year"],
                                     "accounting_regime": "TT200", "pdf_kind": "SCAN" if i == 0 else "NATIVE",
                                     "rights": "synthetic"})
    rows = questions(sources)
    with pytest.raises(ValueError, match="worksheet incomplete"):
        build(rows, sources, profiles, tmp_path)
    for row in rows:
        row.update(review_status="APPROVED", preparer="Alice", independent_reviewer="Bob")
        if row["group"] == "TRAP":
            row.update(expected_disposition="REFUSE", reason="missing")
        else:
            row.update(expected_disposition="ANSWER", normalized_value="12", unit="VND", period="FY2025",
                       scope="CONSOLIDATED", source_observation="source",
                       evidence_regions_json='[{"page":1,"bbox":["1","2","3","4"]},{"page":2,"bbox":["1","2","3","4"]}]',
                       independent_calculation="10 + 2")
    draft = build(rows, sources, profiles, tmp_path)
    assert draft["status"] == "DRAFT_NOT_FROZEN" and len(draft["cases"]) == 60
    evidence = rows[0]["evidence_regions_json"]
    rows[0]["evidence_regions_json"] = '[{"page":3,"bbox":["1","2","3","4"]}]'
    with pytest.raises(ValueError, match="page outside PDF"):
        build(rows, sources, profiles, tmp_path)
    rows[0]["evidence_regions_json"] = '[{"page":1,"bbox":["1","2","300","400"]}]'
    with pytest.raises(ValueError, match="box outside PDF"):
        build(rows, sources, profiles, tmp_path)
    rows[0]["evidence_regions_json"] = evidence
    rows[0]["scope"] = "SEPARATE"
    with pytest.raises(ValueError, match="scope differs"):
        build(rows, sources, profiles, tmp_path)
    rows[0]["scope"] = "CONSOLIDATED"
    (tmp_path / "doc0.pdf").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        build(rows, sources, profiles, tmp_path)
