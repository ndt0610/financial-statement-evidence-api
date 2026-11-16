# M3 error analysis — holdout_v1

**Historical pre-fix snapshot.** The current `benchmarks/results_v1.json` and
`results_v1.md` were regenerated after the M4a router fixes and now show a
60/60 result on this **known regression set**. The numbers and eight failures
below preserve the diagnostic observations before those fixes. Do not treat the
post-fix run as an unseen holdout.

This is a **synthetic diagnostic set**, not an estimate of performance on real
Vietnamese filings. It contains three invented FY2025 TT200 workbooks and 60
curated questions, 12 in each of five groups. The questions are disjoint from
the ten M0 smoke questions, but were authored while the M2 implementation was
known. After this run, holdout_v1 is an observed regression set, not a fresh
blind holdout.

## Measured result

The original pre-fix `python scripts/run_benchmark.py` run produced the
historical measurements below. The linked
[`benchmarks/results_v1.json`](../benchmarks/results_v1.json) and
[`benchmarks/results_v1.md`](../benchmarks/results_v1.md) now contain the
post-fix regression result. The original snapshot
measured 47 answerable questions, 13 refusal/trap questions, 73 gold evidence
cells and 180 timed queries per pipeline.

| Metric | Flat-text baseline | Evidence API core |
|---|---:|---:|
| Numeric Exact Match (number + unit) | 23/47 = 48.94% | 40/47 = 85.11% |
| Evidence Cell Hit Rate | 0/73 = 0% | 66/73 = 90.41% |
| Refusal & Trap Precision | 1/21 = 4.76% | 13/19 = 68.42% |
| Trap detection rate | 1/13 = 7.69% | 13/13 = 100% |
| Full contract accuracy | 0/60 = 0% | 52/60 = 86.67% |
| Query latency p50 / p95 | 0.046 / 0.062 ms | 0.046 / 0.072 ms |

Numeric Exact Match requires both `Decimal` value and reported unit to match,
on gold `ANSWERED` cases only. Evidence Cell Hit Rate counts cited gold
`sheet_name`+`cell_range` pairs on answerable cases. Refusal precision is
correctly classified gold traps divided by **all predicted refusals**, so
false refusals reduce it. Trap detection rate is the separate recall-like
measure. Full contract accuracy also checks status, verification, period,
scope, evidence, note text, calculation basis and required findings.

The flat-text baseline intentionally has no cell locator, so its 0% cell hit
and 0% full contract are largely properties of that design. It is a local
lexical top-3 retrieval and number-extraction surrogate, **not** the original
Docling/Ollama tutorial or an LLM baseline. Neither timed path used a model,
embedding, GPU, network or HTTP. The sub-millisecond latency differences are
in-process microbenchmarks, exclude ingestion and are not production latency
claims; no uncertainty interval was estimated.

## Eight observed Evidence API failures

| Case | What happened | Root cause |
|---|---|---|
| S-U1, C-U1, D-U1 | Correct negative discount rows were refused. | The router lacks an alias for “các khoản giảm trừ doanh thu” (B02 code 02). The number parser itself handled the signed cells. |
| C-L1, C-U3 | Shorter “tiền và tương đương tiền” was refused. | Alias table recognizes only “tiền và **các khoản** tương đương tiền”. |
| C-L4 | “Vốn thuộc chủ sở hữu” was refused. | Variant wording is not mapped to B01 code 400. |
| C-L2 | Asked for inventory at the beginning of 2025; returned the end-2025 cell. | Router matched the numeral 2025 and ignored “đầu năm”; gold is B01 E12, 01/01/2025, FY2024. |
| C-R3 | Scope refusal was correct, but its period was wrong. | The refusal period builder synthesized 31/12/2025 instead of the requested opening date 01/01/2025. |

Six of the eight failures were **false refusals on answerable questions**. They
explain the 68.42% refusal precision despite 13/13 trap detection. The other
two are period interpretation errors. Six failures are on the complex layout,
one on the standard file and one on the dirty file. The file-level arithmetic,
negative-number parsing, formula-cache checks and note-link lookups worked for
the covered cases.

The dirty file exposed three distinct protections: missing cache D-R1,
stale cache D-R2/D-R3, and an assets-versus-debt-plus-equity difference of
1,000 nghìn đồng (1,000,000 VND) above the configured 500,000 VND tolerance
in D-M3. The last case carries `RECONCILIATION_WARNING`; a direct assets
lookup can still present the sourced value with its finding, while the
reconciliation question is refused. This is a review signal, not a finding of
financial misconduct.

## Limits and next evaluation gate

- Only three invented TT200 FY2025 workbooks, one fiscal calendar, one
  independent authoring pass and 60 questions were measured. There are no
  licensed real XLSX filings, human adjudicators, confidence intervals, PDF
  cases, OCR cases or bank-specific statements in this set.
- The router is a finite phrase table. `note_ref` resolution currently depends
  on a simple Roman-section pattern and exact document-local note entries.
- The API stores local files and an index but has no multi-user authorization.
- Do not tune on these eight failures and then report holdout_v1 as unseen.
  Keep it for regression, create a new blind set, and add legally usable real
  filings with independent human labels. For a tutorial comparison, separately
  freeze the exact tutorial commit, model, embedding, prompt, hardware and
  costs before running the original Docling/Ollama pipeline.

The percentages above belong in this technical report only. They should not
be presented as CV evidence of performance on real financial statements.
