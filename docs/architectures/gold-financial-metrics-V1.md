# Gold: choosing a defensible financial value

Silver keeps every accepted XBRL occurrence. Gold selects which occurrence can represent a metric for a filing and exact period. The dbt rules cover eight mapped metrics, publish three final views, and record issues when the evidence cannot support a choice.

## Inputs and metric set

Gold reads the active Silver catalog, stored filing metadata from the saved submissions response, and fiscal metadata extracted from the verified XBRL document. Those sources stay separate because the SEC response establishes form and report date, while DEI facts in the active instance establish fiscal focus. Gold does not infer fiscal focus from an accession or decide that a roughly 90-day duration is automatically the current quarter. The mappings live in [`transform/seeds/metric_concept_map.csv`](../../transform/seeds/metric_concept_map.csv), where concept priority is visible and reviewable.

| Metric code | Expected period | Unit | Critical for filing quality? |
| --- | --- | --- | --- |
| `revenue` | duration | USD | Yes |
| `net_income` | duration | USD | Yes |
| `diluted_eps` | duration | USD/shares | Yes |
| `total_assets` | instant | USD | Yes |
| `total_liabilities` | instant | USD | Yes |
| `cash_and_equivalents` | instant | USD | No |
| `operating_cash_flow` | duration | USD | No |
| `capital_expenditures` | duration | USD | No |

The marts cover mapped US-GAAP concepts. Issuer extensions, operating income, equity, ratios, and free cash flow are outside the current metric set.

The seed is a **candidate map**, not a table of final answers. For revenue it allows `RevenueFromContractWithCustomerExcludingAssessedTax`, `SalesRevenueNet`, then `Revenues` in that priority order. A higher-priority concept cannot win over a lower-priority one that reports a different eligible value for the same period; that is a conflict to inspect. Keeping mappings in a seed makes the supported vocabulary reviewable without burying it in several `CASE` expressions.

The path from active facts to the marts runs through these models:

| Step | Models and row grain |
| --- | --- |
| Active input | [Staging models](../../transform/models/staging/) expose active filings, Silver fact occurrences and their dimension rows, and verified filing/fiscal metadata without making a metric choice. |
| Candidate and accuracy gate | [`int_financial_metric_candidates.sql`](../../transform/models/intermediate/int_financial_metric_candidates.sql) keeps mapped occurrences; [`int_financial_metric_candidate_accuracy.sql`](../../transform/models/intermediate/int_financial_metric_candidate_accuracy.sql) evaluates their raw accuracy attributes. |
| Reported choice | [Revenue](../../transform/models/intermediate/int_reported_revenue.sql), [income](../../transform/models/intermediate/int_reported_income_metrics.sql), [balance sheet](../../transform/models/intermediate/int_reported_balance_sheet_metrics.sql), and [cash flow](../../transform/models/intermediate/int_reported_cash_flow_metrics.sql) choose one supported value per eligible group while retaining ordered supporting IDs. |
| Period and derivation | [`int_reported_financial_periods.sql`](../../transform/models/intermediate/int_reported_financial_periods.sql) labels exact reported periods. [Q2/Q3 cash-flow](../../transform/models/intermediate/int_derived_quarterly_cash_flow.sql) and [Q4](../../transform/models/intermediate/int_derived_q4_financial_metrics.sql) models produce only supported derived quarters; companion issue models record failures. |
| Published result | [`fct_financial_metrics.sql`](../../transform/models/marts/fct_financial_metrics.sql), [`dim_filings.sql`](../../transform/models/marts/dim_filings.sql), and [`metric_quality_issues.sql`](../../transform/models/marts/metric_quality_issues.sql) expose selected values, per-filing quality, and individual issues. |

## How a reported value is selected

`int_financial_metric_candidates` joins mapped, non-nil facts to their active source document. It keeps one row per occurrence and counts dimensions without multiplying fact rows. The accuracy model keeps the raw `decimals` and `precision` strings. It requires exactly one usable attribute: an integer or uppercase `INF` for `decimals`, or a non-negative integer or uppercase `INF` for `precision`. Zero precision means unknown accuracy. Missing, malformed, or simultaneous attributes make the candidate unusable; the raw strings and issue stay available for inspection.

The check is more than “numeric-looking value.” The fact must belong to the active document and issuer, have the right duration or instant kind, use the exact USD or USD/share expression expected for that metric, and have no dimensions for these consolidated selections. Reported values with unrepresentable precision were already rejected in Silver; Gold also checks whether the XBRL accuracy attributes can support a trusted choice. An otherwise eligible period group with even one unusable accuracy attribute is withheld rather than choosing the cleaner duplicate and hiding the bad one.

The reported selection models then require matching issuer identity, verified filing metadata, appropriate form and report date, exact period boundaries, no dimensions, and the expected unit. Agreement is checked across eligible occurrences for the same metric and period. If their values or accuracy attributes conflict, concept priority does **not** decide the answer; the group produces an issue and no selected reported row. When they agree, one occurrence supplies the displayed value, while the ordered supporting occurrence IDs retain the others. Priority only makes that representative deterministic.

For example, two revenue facts for the same accession and dates may report the same USD value under different mapped concepts. One representative row is useful for a dashboard, but both occurrence IDs remain in `current_supporting_occurrence_ids`. If those values disagree, the issue view retains the source IDs and the metric is withheld. Deduplicating Silver first would remove the evidence needed to tell those cases apart.

Period classification uses the verified fiscal year/period focus together with the reported dates and form. Annual, quarter, year-to-date, and instant values retain different labels. An instant balance must match the report date. A quarter and a longer year-to-date value in the same 10-Q are not interchangeable. Missing or conflicting fiscal evidence leaves a row unclassified and visible through the issue view instead of turning a guessed period into a final metric.

V1 uses duration windows of 350–380 days for FY, 80–100 for a quarter, 170–200 for Q2 year to date, and 260–290 for Q3 year to date. They are checked **after** form, fiscal focus, source lineage, and report-date agreement; duration alone does not name a fiscal period. Competing eligible periods for the same filing, metric, and classification are reported as ambiguous. An instant only needs its observation date to match the verified report date, so lack of fiscal focus by itself does not turn a valid balance into a duration guess.

## Derived quarters

Some cash-flow filings give cumulative values without a standalone quarter. For Q2/Q3 operating cash flow and capex, Gold can subtract the compatible previous cumulative amount. For Q4 revenue, net income, operating cash flow, and capex, it can subtract Q3 year-to-date from the annual value. Both paths require compatible company, fiscal year, metric, dates, units, and unambiguous inputs. An existing reported quarter takes precedence. Missing or conflicting predecessors produce derivation issues; no zero or cross-company substitute is used.

The issue models count all possible predecessor candidates **before** checking compatibility. If two Q3 year-to-date filings could supply an annual-minus-Q3 calculation, Gold reports ambiguity instead of quietly picking whichever one passes a later unit filter. A single candidate still has to align on period start, report date, form, unit, dimensions, and a plausible quarter length. The subtraction is cast back to exact `DECIMAL(38,18)`; an out-of-range result becomes an issue. A missing predecessor in a bounded dataset therefore leaves the reported annual/YTD values intact and the standalone quarter absent.

Derived rows are labeled `derived_quarter` and retain the current and predecessor values, accessions, documents, checksums, supporting occurrence IDs, and calculation method. Diluted EPS is reported only; subtracting cumulative EPS would not be a safe general rule.

## Trace one Q4 value back to the filing

Apple's FY 2025 operating cash flow comes from two filings: the annual cumulative value and the Q3 year-to-date value that Gold subtracts for Q4. The numbers and IDs below are in the real cohort's ignored local `pilot-apple-gold-after-v3.json` snapshot and [acceptance report](../validation/cohort-V1.md); the [two-company run](../validation/multi-company-acceptance-V1.md) independently checked the same pair.

1. The SEC listings identify issuer CIK `0000320193` and two different accessions. The annual `10-K` is `0000320193-25-000079`, filed `2025-10-31` with report date `2025-09-27`. The Q3 `10-Q` is `0000320193-25-000073`, filed `2025-08-01` with report date `2025-06-28`. These filing dates select the documents; they do not define the cash-flow periods.
2. The Bronze manifests mark both source archives `COMPLETE` and parser-ready. They record the verified XML bytes as `aapl-20250927_htm.xml` with SHA-256 `e1076735f1c81bc96d5c1ff6e1a9d23515d6eacf52b405cb1f7da3e379ac533b` and `aapl-20250628_htm.xml` with SHA-256 `a33da25d38ddc3c39fcb80b996a26fb562b8938f942f7a41a5f156339a7d70c9`. Source verification happens before interpreting a number.
3. Parser-3 Silver keeps the annual `NetCashProvidedByUsedInOperatingActivities` occurrence at root ordinal 515: raw `111482000000`, `decimals="-6"`, USD, duration `2024-09-29` through `2025-09-27`. Its occurrence ID is `8d666449ebbf6e5a9e18a56f4a8690e82ca9b8ffb566f279538a6195ba859398`. The Q3 document's occurrence at ordinal 497 is raw `81754000000`, the same USD unit and period start, ending `2025-06-28`; its ID is `d294ba443cdd88a410b335adddc09b5c9d543bfd3cf11766ae8dfeb626b85f2f`. Both are exact `DECIMAL(38,18)` values in `facts.parquet`. The active pointers select those checked document/hash/parser versions; a similarly named file outside the active version would not be used.
4. `silver.filing_metadata` keeps each form and report date from its saved SEC listing. `silver.filing_fiscal_metadata` version 3 resolves the same verified documents to FY 2025 `FY` and FY 2025 `Q3`, with DEI period ends matching their report dates. The catalog exposes the two active versions to dbt. This is why the Q3 value is a predecessor from the **same issuer and fiscal year**, rather than merely the nearest smaller amount.
5. [`int_reported_financial_periods.sql`](../../transform/models/intermediate/int_reported_financial_periods.sql) classifies the annual and Q3 year-to-date rows. [`int_q4_financial_metric_derivation_issues.sql`](../../transform/models/intermediate/int_q4_financial_metric_derivation_issues.sql) checks predecessor count and compatibility before [`int_derived_q4_financial_metrics.sql`](../../transform/models/intermediate/int_derived_q4_financial_metrics.sql) subtracts them. Both inputs start `2024-09-29`; Q4 begins the day after the Q3 end. The stored calculation is `111,482,000,000 − 81,754,000,000 = 29,728,000,000` USD for `2025-06-29` through `2025-09-27`. Gold records `ANNUAL_MINUS_Q3_YTD`, both accessions, both checksums, both exact operands, and both supporting occurrence arrays.
6. [`fct_financial_metrics.sql`](../../transform/models/marts/fct_financial_metrics.sql) publishes that row with `value_origin='derived'` and `period_label='derived_quarter'`, alongside the reported annual row. The [dashboard reader](../../src/sec_edgar_lakehouse/dashboard/dashboard_read.py) returns the Decimal and ordered arrays from Gold; the [UI](../../src/sec_edgar_lakehouse/dashboard/dashboard_app.py) formats the visible amount but leaves the fixed-scale value and raw lineage in the full record. It does not redo the subtraction.

The [cohort audit](../validation/cohort-V1.md) checked every selected metric-support occurrence against its Silver company, accession, document, checksum, and Decimal value, and checked every derived output against its two stored operands. Missing or conflicting candidates remain visible as issues.

## What the final views mean

| View | Grain | Use |
| --- | --- | --- |
| `gold.fct_financial_metrics` | One company/accession/metric/exact selected or derived period | Exact `DECIMAL(38,18)` value, unit, origin, period, rule versions, and source lineage. |
| `gold.dim_filings` | One verified active CIK/accession | Filing/fiscal metadata, source identity, counts, missing critical metrics, and Gold quality status. |
| `gold.metric_quality_issues` | One upstream issue occurrence | Selection, accuracy, period, and derivation problems with severity and available evidence. Repeated issues remain separate. |
| `gold.dim_companies` | One company with active filings | Name, tickers, SIC code and industry, SEC sector, fiscal year end, and filing coverage from the newest saved SEC submissions response. |
| `gold.fct_company_financials` | One company/fiscal year/fiscal period (FY, Q1–Q4) | Standalone revenue, net income, diluted EPS, cash flow, capex, free cash flow, balance-sheet totals, margins, and year-over-year revenue growth side by side. |

`dim_filings` includes active filings even if no metric is selected. Its five-metric critical profile requires reported revenue, net income, and diluted EPS for the current annual/quarter period, plus assets and liabilities at the report-date instant. It marks a filing `FAILED` when filing metadata or its report date is missing, the form is unsupported, or no metric was selected. Missing critical metrics or an error issue makes it `PARTIAL`; otherwise it is `COMPLETE`. Missing fiscal metadata may prevent a duration from qualifying, but it is not itself a hard-coded failure status. A `COMPLETE` filing can still have supplemental warnings. This quality status is **not** the pipeline execution status or the dbt result.

## Company dimension and company financials

The first three views answer "what did this filing report?" The last two answer "how did this company do?" without repeating any financial rule.

`dim_companies` reads `silver.company_profiles`, which the metadata stage fills from the SEC submissions response saved for each company run. The loader rechecks the saved bytes against the run record's SHA-256 before reading the name, tickers, SIC code and description, and `ownerOrg`. One row is kept per company and response checksum, so an identical rerun is reused and a newer response becomes another snapshot; the dimension uses the newest. `sector` is the SEC's industry office with its two-digit code split off (`06 Technology` becomes code `06`, sector `Technology`). It is not GICS. That is why semiconductor companies such as NVIDIA appear under Manufacturing. Empty SEC fields stay NULL; nothing is filled in from another source.

`fct_company_financials` takes, for each company and fiscal period, the standalone value of each metric from `fct_financial_metrics`: the `annual` row for FY, and the `quarter` or `derived_quarter` row for Q1–Q4. Year-to-date rows are left out. Balance-sheet totals come from the same filing's report-date instant, so a 10-K's year-end balance appears on both its FY and Q4 rows. Free cash flow is operating cash flow minus capital expenditures and is NULL unless both exist. Net margin, free-cash-flow margin, and revenue growth against the same period a year earlier are floating-point ratios, computed only with positive revenue; the metric values themselves stay exact Decimals. Q4 diluted EPS is always NULL, because EPS is never derived. Tests check one row per company and period, one source value per metric, matching period dates, value-for-value reconciliation with `fct_financial_metrics`, and that every company has a `dim_companies` row.

There are no fabricated zero-valued metric rows. Source values stay exact Decimals with their original unit expressions, and Gold does not convert currencies or sum segments to manufacture a consolidated total. The dashboard reads these final views without recalculating their financial rules.

The views also separate two kinds of “complete.” `dim_filings.gold_quality_status` checks the reported critical profile and error issues for one accession. `metric_quality_issues` can still contain supplemental warnings, such as a missing cumulative predecessor for cash flow. The end-to-end execution summary answers whether the stages ran and dbt built; it cannot stand in for either of those financial checks.

## Reproducibility and limits

dbt models and tests live under `transform/models/` and `transform/tests/`. Tests check source keys, grain, eligible selection, issue shape, period classification, exact calculations, and current/predecessor lineage. The concept map and rule-version columns make a result explainable when code changes; rerunning against the same active inputs produces the same logical rows.

The SQL is organized as staging views over Silver, intermediate candidates and selection/derivation models, then the five marts. Staging stays as views, so DuckDB never holds a second copy of the Silver facts; the intermediate models and marts are stored as tables, so each is computed once per build and tests and readers use the stored rows. With every model as a view, each test and each dashboard read recomputed the whole chain over about 600,000 facts: the cohort build took 34m27s and the dashboard could run out of its 4GB memory limit. As tables, the same 562 nodes build in about 18 seconds. The staging layer preserves Silver names and types; it does not make a financial choice. Intermediate models keep candidates and issue evidence available for review. Final marts publish only selected or supported derived rows, with tests checking that each occurrence and predecessor still belongs to the right active company and accession. This is why financial logic lives in dbt rather than a second Python transformation path.

The models retain accession history. They do not pick the latest amendment or restatement, reconcile different accounting policies between companies, or create full-year coverage from a bounded filing selection. The [Apple/Microsoft acceptance report](../validation/multi-company-acceptance-V1.md) shows actual rows, derivations, and missing-predecessor warnings from four selected filings.
