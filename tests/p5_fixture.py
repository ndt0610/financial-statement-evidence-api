"""PNJ manual-transcription acceptance input; never imported by product code."""

import csv
import json
from decimal import Decimal
from pathlib import Path

from app.models import Evidence, PDFLocator, ReportScope
from app.services.analysis_book import AnalysisBook, LINE_CONCEPTS, SourceDatum


ROOT = Path(__file__).resolve().parents[1] / "docs" / "pnj_precode"
PAGE = {
    2020: ("ar2021", {"B01-DN": 68, "B02-DN": 69, "B03-DN": 70}),
    2021: ("ar2021", {"B01-DN": 68, "B02-DN": 69, "B03-DN": 70}),
    2022: ("fs2022", {"B01-DN": 8, "B02-DN": 11, "B03-DN": 12}),
    2023: ("fs2023", {"B01-DN": 7, "B02-DN": 10, "B03-DN": 12}),
    2024: ("fs2024", {"B01-DN": 7, "B02-DN": 10, "B03-DN": 11}),
    2025: ("fs2025", {"B01-DN": 7, "B02-DN": 10, "B03-DN": 11}),
}


def pnj_book() -> AnalysisBook:
    manifest = json.loads((ROOT / "source_manifest.json").read_text(encoding="utf-8"))
    hashes = {item["id"]: item["sha256"] for item in manifest["sources"]}
    reverse = {concept: (form, code) for (form, code), concept in LINE_CONCEPTS.items()}
    rows = list(csv.DictReader((ROOT / "manual_transcription_wide.csv").open(encoding="utf-8-sig")))
    datums = []
    for row in rows:
        concept = row["metric"]
        if concept not in reverse and concept != "term_deposits":
            continue
        form, code = reverse.get(concept, ("NOTE12", "term_deposits"))
        for label, value in row.items():
            if label == "metric" or value == "":
                continue
            year = int(label)
            source, pages = PAGE[year]
            if concept == "term_deposits":
                source, page = (("ar2025",123) if year == 2025 else
                                ("ar2024",106) if year in (2023,2024) else
                                ("ar2022",112) if year == 2022 else ("ar2021",68))
            else:
                page = pages[form]
            evidence = Evidence(document_hash=hashes[source], locator=PDFLocator(
                page_number=page, table_id=form, region=f"mã {code}; cột {year}"),
                observed_text=f"Manual transcription: {value} VND")
            datums.append(SourceDatum(concept, year, Decimal(value), (evidence,),
                                     "analyst_transcribed_pending_separate_signoff", "PNJ", ReportScope.CONSOLIDATED,
                                     f"{year}-12-31", f"{form}:{code}"))
    # The public note provides a separate recognized-rent figure, not a B02 line.
    calc = json.loads((ROOT / "independent_calculations.json").read_text(encoding="utf-8"))
    for year in (2024, 2025):
        value = calc["lease_cover"][str(year)]["recognized_store_rent_vnd"]
        evidence = Evidence(document_hash=hashes["ar2025"], locator=PDFLocator(
            page_number=128, table_id="NOTE32", region=f"chi phí thuê cửa hàng cột {year}"), observed_text=value)
        datums.append(SourceDatum("store_rent", year, Decimal(value), (evidence,),
                                 "analyst_transcribed_pending_separate_signoff", "PNJ", ReportScope.CONSOLIDATED,
                                 f"{year}-12-31", "NOTE32:store_rent"))
        future=calc["lease_cover"][str(year)]["commitment_next_12_months_vnd"]
        commitment=Evidence(document_hash=hashes["ar2025"],locator=PDFLocator(
            page_number=130,table_id="NOTE37",region=f"minimum rent commitment within next year at {year}-12-31"),
            observed_text=future)
        datums.append(SourceDatum("rent_commitment_next_12m",year,Decimal(future),(commitment,),
                                 "analyst_transcribed_pending_separate_signoff","PNJ",ReportScope.CONSOLIDATED,
                                 f"{year}-12-31","NOTE37:future_minimum_rent"))
    for year, raw, finished, pledged in (
        (2024, "634610808014", "8466765298103", "2930000000000"),
        (2025, "4820465556971", "7189811338988", "4361000000000"),
    ):
        for concept, value in (("raw_materials", raw), ("finished_goods", finished),
                               ("pledged_assets_book_value", pledged)):
            evidence = Evidence(document_hash=hashes["ar2025"], locator=PDFLocator(
                page_number=121, table_id="NOTE8", region=f"{concept} cột {year}"), observed_text=value)
            datums.append(SourceDatum(concept, year, Decimal(value), (evidence,),
                                     "analyst_transcribed_pending_separate_signoff", "PNJ", ReportScope.CONSOLIDATED,
                                     f"{year}-12-31", f"NOTE8:{concept}"))
    # Note 19 and the debt section of B01 present bank borrowings as short term
    # for these two acceptance years; the zero is an analyst inference, pending signoff.
    for year in (2024,2025):
        evidence=Evidence(document_hash=hashes["ar2025"],locator=PDFLocator(
            page_number=125,table_id="NOTE19",region=f"vay ngắn hạn; không có dòng vay dài hạn; {year}"),
            observed_text="No long-term interest borrowing presented")
        datums.append(SourceDatum("long_borrowings",year,Decimal(0),(evidence,),
                                 "analyst_transcribed_pending_separate_signoff","PNJ",ReportScope.CONSOLIDATED,
                                 f"{year}-12-31","NOTE19:long_borrowings_assessed_zero"))
    equity=calc["funding_bridge_2024_2025_vnd"]["equity"]
    for concept, source_key in (("equity_net_income","net_income_vnd"),
                                ("new_share_capital_net","new_share_capital_net_vnd"),
                                ("declared_dividends","declared_dividends_vnd"),
                                ("welfare_allocation","welfare_allocation_vnd"),
                                ("board_management_allocation","board_management_allocation_vnd")):
        evidence=Evidence(document_hash=hashes["ar2025"],locator=PDFLocator(
            page_number=127,table_id="NOTE24",region=f"equity roll-forward {concept} 2025"),
            observed_text=equity[source_key])
        datums.append(SourceDatum(concept,2025,Decimal(equity[source_key]),(evidence,),
                                 "analyst_transcribed_pending_separate_signoff","PNJ",ReportScope.CONSOLIDATED,
                                 "2025-12-31",f"NOTE24:{concept}"))
    return AnalysisBook(datums, analysis_years=[2021, 2022, 2023, 2024, 2025])
