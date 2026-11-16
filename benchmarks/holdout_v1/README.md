# holdout_v1 dataset card

All three XLSX files are wholly invented and labeled `SYNTHETIC` in the first
row. They are TT200-style non-bank FY2025 examples for software diagnosis,
not licensed or audited company filings. The names, figures and note prose
were written for this repository; no financial data were copied from a real
report. The workbook layout is simplified and cannot establish regulatory
conformity.

| File | Scope / unit | Structural probe |
|---|---|---|
| `standard_tt200.xlsx` | Separate / triệu đồng | Clean B01/B02/B09 with prior-period columns and note links |
| `complex_unit_trap.xlsx` | Consolidated / đồng | Merged period headings, long VND amounts, alias variants and parenthesized negatives |
| `audit_trap.xlsx` | Separate / nghìn đồng | Correct, missing and stale formula caches; 1,000,000 VND balance difference above the declared 500,000 VND tolerance |

`gold.json` has 60 human-curated questions: 12 each for lookup,
unit/negative, metric/reconciliation, numeric+note, and refusal/trap. There are
20 questions per workbook. The labels were checked against workbook cells and
independent arithmetic in `tests/test_m3_holdout.py`. They do not reuse the M0
smoke questions. Their author knew the M2 implementation, so this set is not
blinded and must not be called an unbiased real-world holdout.

To reproduce the byte-identical files and labels:

```powershell
python benchmarks/holdout_v1/build_holdout.py
python benchmarks/holdout_v1/build_gold.py
python -m pytest -q tests/test_m3_holdout.py
```

`manifest.json` records each SHA-256 hash, scope, unit and VND reconciliation
tolerance. The benchmark runner refuses a modified workbook hash. The three
files stay separate from M0's `sample_bctc_2025.xlsx`.
