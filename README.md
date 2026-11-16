# Financial Statement Evidence API

An inspectable analysis workbench for Vietnamese financial statements. It turns spreadsheet and PDF inputs into versioned facts, keeps evidence linked to source cells or reviewed PDF locations, and runs deterministic calculations with explicit units and periods.

## Why this project

Financial answers are easier to trust when the reader can trace each value. This project combines statement ingestion, validation, financial calculations, and human review instead of returning a number without its source.

## Main workflow

~~~mermaid
flowchart LR
  F[TT200-style XLSX or PDF] --> I[Ingestion and normalization]
  I --> V[Versioned facts with source locators]
  V --> Q[Question routing]
  Q --> C[Decimal-based calculations]
  C --> E[Evidence and consistency checks]
  E --> A[Answer, finding, or refusal]
  H[Human review] --> V
~~~

- XLSX facts retain source-cell references and document hashes.
- PDF facts keep page and region provenance; extracted values remain pending until reviewed.
- The analysis layer uses Decimal and explicit period, unit, scope, and conflict policies.
- The API can refuse when evidence is missing or a calculation cannot be supported.

## Example scope

The project implements core financial indicators, accounting bridges, DuPont analysis, and review signals for comparative financial analysis. The formula checker is intentionally limited to supported patterns such as SUM(A1:A5) and simple cell addition/subtraction; unsupported spreadsheet formulas remain unverified.

## Synthetic regression snapshot

The holdout_v1 set contains invented FY2025 workbooks and 60 diagnostic questions. The reported 60/60 result is on a known regression set authored with knowledge of the implementation, not on a fresh blind benchmark. The flat-text comparison also lacks cell-location support, so evidence-cell results are not an apples-to-apples capability test. See docs/error_analysis.md.

A small real-document pilot is described in docs/M4B_REAL_PILOT.md: two public statements, 56/60 labeled values extracted, and 22/22 arithmetic checks. This is exploratory development evidence, not independent validation or a production accuracy claim.

## Run locally

    python -m pip install -r requirements-m2.txt
    python -m pytest tests -q
    uvicorn app.api.main:app --reload

Open http://127.0.0.1:8000/analysis for the analysis workbench and /audit for PDF review. API contracts and example requests are in docs/M2_API.md.

To build the synthetic four-statement health-review fixture:

    python -m scripts.build_m4_fixture

See docs/M4_HEALTH_REVIEW.md for the health-review workflow and docs/M5_TENDER_REVIEW.md for the criteria prototype.

## Structure

- app/adapters/ — spreadsheet and PDF readers.
- app/models/ — financial facts and request/response contracts.
- app/services/ — normalization, evidence checks, routing, calculations, and review services.
- app/api/ — FastAPI routes.
- app/web/ — browser workbench.
- benchmarks/holdout_v1/ — synthetic benchmark generation.
- tests/ — API, review workflow, and regression checks.

## Limits

This is a local research prototype. It has no authentication or multi-user authorization. Synthetic benchmark results do not establish extraction accuracy on arbitrary filings. Review signals support investigation; they do not determine credit, tender, fraud, or investment decisions.
