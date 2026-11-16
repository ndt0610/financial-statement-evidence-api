"""Only signed, source-preserving PDF sign differences may be classified equivalent."""

from decimal import Decimal

from app.services.analysis_book import SourceDatum
from app.services.time_series_stitcher import sign_convention_equivalent
from app.models.financial_fact import ReportScope


def datum(value: str, status: str) -> SourceDatum:
    return SourceDatum("cogs", 2022, Decimal(value), (), status, "PNJ",
                       ReportScope.CONSOLIDATED, "2022-12-31", "B02-DN:11")


def test_sign_equivalence_requires_reviewed_pdf_expense_and_equal_magnitude():
    versions = [(2022, datum("-27949348024381", "HUMAN_VERIFIED"), "a"),
                (2023, datum("27949348024381", "HUMAN_VERIFIED"), "b")]
    assert sign_convention_equivalent("cogs", versions, {"a", "b"})
    assert not sign_convention_equivalent("assets", versions, {"a", "b"})
    assert not sign_convention_equivalent("cogs", versions, {"a"})
    assert not sign_convention_equivalent("cogs", [versions[0],
        (2023, datum("27949348024381", "EXTRACTED"), "b")], {"a", "b"})
    assert not sign_convention_equivalent("cogs", [versions[0],
        (2023, datum("27949348024380", "HUMAN_VERIFIED"), "b")], {"a", "b"})
