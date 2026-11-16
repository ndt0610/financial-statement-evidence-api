# M2 Evidence API

Install with `python -m pip install -r requirements-m2.txt`, then run
`uvicorn app.api.main:app`. `POST /v1/documents/ingest` accepts a multipart
`.xlsx` upload (`file`) and optional `accounting_regime`, `data_origin`, and
`company_id` form fields. The marked synthetic FY2025 fixture defaults to
TT200; other files must declare their regime and origin. The response returns
the SHA-256 document hash and state counts.

`POST /v1/qa/query` takes JSON such as:

```json
{"document_hash":"<hash from ingest>","question":"Tổng tài sản cuối 2025 tăng bao nhiêu so với cuối 2024?"}
```

The response includes `answer`, `status`, `verification_status`, string-encoded
Decimal `value`, `unit`, `period`, `report_scope`, `calculation`, and cell-level
`evidences`. A missing or stale formula cache keeps its state and causes a
refusal even if a trial calculation exists. A missing year can have an empty
evidence list; a scope mismatch points to the report title cell.

Uploads and the fact/note index are stored under `data/uploads` by hash and
can be read after an app restart. This local store has no authentication or
multi-user access control; deployment is a later milestone. Query routing is
rule-based for the supported TT200 line codes and wording, not a general
Vietnamese language model.

The calculator uses `Decimal` throughout. For ROA/ROE the API uses average
opening and closing assets/equity unless the question explicitly asks for a
closing-balance basis, and the returned formula names that choice. The
[CFA Institute ratio list](https://www.cfainstitute.org/sites/default/files/-/media/documents/support/programs/cfa/cfa_program_level_ii_financial_ratio_list.pdf)
uses average balances for these ratios. FY2025 M0 lacks after-tax profit code
60, so ROA/ROE and Net Margin queries on that fixture correctly refuse; the
calculator functions are unit-tested with constructed facts. These synthetic
smoke results are not a benchmark on real financial statements.
