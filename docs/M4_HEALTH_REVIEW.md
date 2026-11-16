# M4a — financial health review on synthetic XLSX

## Run

```powershell
python -m scripts.build_m4_fixture
python -m scripts.export_m4_sample
python -m pytest -q
uvicorn app.api.main:app
```

Upload `data/bctc_health_case_2025.xlsx` to `POST /v1/documents/ingest` with
`accounting_regime=TT200` and `data_origin=SYNTHETIC`. Then call
`POST /v1/health-reviews` with:

```json
{"document_hash": "<hash returned by ingest>", "year": 2025}
```

The endpoint reads the persisted fact index after an app restart. Its response
contains `document_metadata`, `reconciliation_checks`, `indicators`, and
`review_signals`. Each indicator includes the namespaced B01/B02/B03 input
codes, period, source cell, formula, verification status and Decimal output.
The current endpoint supports the document fiscal year; earlier comparative
figures are used to compute growth.
The generated [sample JSON](m4_sample_report.json) contains the full response
for the synthetic reference case.

## Synthetic reference case

The fixture has positive after-tax income of 65 million VND and negative
operating cash flow of 80 million VND in FY2025. It also has current assets
of 500 million and current liabilities of 300 million VND. Expected outputs:

| Indicator/check | Expected result |
|---|---:|
| Net working capital | 200 million VND |
| Revenue growth | 30% |
| Trade receivables growth (B01:131) | 116.67% approximately |
| Total current receivables growth (B01:130) | 70% |
| B03:70 vs B01:110 | PASS (140 million VND each) |

The two review signals say only that earnings are positive while operating
cash flow is negative, and trade receivables grew faster than revenue. Neither
is a finding of fraud. A mismatch in B03:70 versus B01:110 creates a
`RECONCILIATION_WARNING` with source cells from both statements.
If B01:131 is absent, the comparison uses B01:130 and labels it explicitly as
a broader proxy, because total receivables include items other than trade
credit. Both growth series remain visible when both lines are available.

## Scope and limitations

- The quick ratio is the simplified `(current assets - inventory) / current
  liabilities`; some analysts also exclude other less-liquid current assets.
- CFO / net income is omitted when net income is nonpositive. Zero or missing
  denominators, missing B03, and unverified formula caches do not produce a
  numeric indicator or a confident cash-flow signal.
- Growth uses the current and comparative years in the **same workbook**. It
  does not yet combine separately filed documents.
- All M4a fixtures are synthetic. The corrected M3 regression run was tuned
  on its own observed errors; validation on public filings is M4b.
