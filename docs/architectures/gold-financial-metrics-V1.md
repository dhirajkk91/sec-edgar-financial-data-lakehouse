# Gold: choosing a defensible financial value

Silver keeps every accepted XBRL occurrence. Gold answers the harder question: which of those occurrences can represent a metric for this filing and exact period? dbt owns those rules. The result is deliberately small: eight mapped metrics, three final views, and explicit issue rows for evidence the rules cannot safely use.

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

The mapped US-GAAP concepts are a supported subset, not a claim to cover every issuer extension. There is no operating-income, equity, ratio, or free-cash-flow output in the current marts.

The seed is a **candidate map**, not a table of final answers. For revenue it allows `RevenueFromContractWithCustomerExcludingAssessedTax`, `SalesRevenueNet`, then `Revenues` in that priority order. A higher-priority concept cannot win over a lower-priority one that reports a different eligible value for the same period; that is a conflict to inspect. Keeping mappings in a seed makes the supported vocabulary reviewable without burying it in several `CASE` expressions.

## How a reported value is selected

`int_financial_metric_candidates` joins mapped, non-nil facts to their active source document. It keeps one row per occurrence and counts dimensions without multiplying fact rows. The accuracy model keeps the raw `decimals` and `precision` strings. It requires exactly one usable attribute: an integer or uppercase `INF` for `decimals`, or a non-negative integer or uppercase `INF` for `precision`. Zero precision means unknown accuracy. Missing, malformed, or simultaneous attributes make the candidate unusable; the raw strings and issue stay available for inspection.

The check is more than “numeric-looking value.” The fact must belong to the active document and issuer, have the right duration or instant kind, use the exact USD or USD/share expression expected for that metric, and have no dimensions for these consolidated selections. Reported values with unrepresentable precision were already rejected in Silver; Gold also checks whether the XBRL accuracy attributes can support a trusted choice. An otherwise eligible period group with even one unusable accuracy attribute is withheld rather than choosing the cleaner duplicate and hiding the bad one.

The reported selection models then require matching issuer identity, verified filing metadata, appropriate form and report date, exact period boundaries, no dimensions, and the expected unit. Agreement is checked across eligible occurrences for the same metric and period. If their values or accuracy attributes conflict, concept priority does **not** decide the answer; the group produces an issue and no selected reported row. When they agree, one occurrence supplies the displayed value, while the ordered supporting occurrence IDs retain the others. Priority only makes that representative deterministic.

For example, two revenue facts for the same accession and dates may report the same USD value under different mapped concepts. One representative row is useful for a dashboard, but both occurrence IDs remain in `current_supporting_occurrence_ids`. If those values disagree, the issue view retains the source IDs and the metric is withheld. Deduplicating Silver first would remove the evidence needed to tell those cases apart.

Period classification uses the verified fiscal year/period focus together with the reported dates and form. Annual, quarter, year-to-date, and instant values retain different labels. An instant balance must match the report date. A quarter and a longer year-to-date value in the same 10-Q are not interchangeable. Missing or conflicting fiscal evidence leaves a row unclassified and visible through the issue view instead of turning a guessed period into a final metric.

Current V1 duration windows are intentionally bounded: 350–380 days for FY, 80–100 for a quarter, 170–200 for Q2 year to date, and 260–290 for Q3 year to date. They are checked **after** form, fiscal focus, source lineage, and report-date agreement; duration alone does not name a fiscal period. Competing eligible periods for the same filing, metric, and classification are reported as ambiguous. An instant only needs its observation date to match the verified report date, so lack of fiscal focus by itself does not turn a valid balance into a duration guess.

## Derived quarters

Some cash-flow filings give cumulative values without a standalone quarter. For Q2/Q3 operating cash flow and capex, Gold can subtract the compatible previous cumulative amount. For Q4 revenue, net income, operating cash flow, and capex, it can subtract Q3 year-to-date from the annual value. Both paths require compatible company, fiscal year, metric, dates, units, and unambiguous inputs. An existing reported quarter takes precedence. Missing or conflicting predecessors produce derivation issues; no zero or cross-company substitute is used.

The issue models count all possible predecessor candidates **before** checking compatibility. If two Q3 year-to-date filings could supply an annual-minus-Q3 calculation, Gold reports ambiguity instead of quietly picking whichever one passes a later unit filter. A single candidate still has to align on period start, report date, form, unit, dimensions, and a plausible quarter length. The subtraction is cast back to exact `DECIMAL(38,18)`; an out-of-range result becomes an issue. A missing predecessor in a bounded dataset therefore leaves the reported annual/YTD values intact and the standalone quarter absent.

Derived rows are labeled `derived_quarter` and retain the current and predecessor values, accessions, documents, checksums, supporting occurrence IDs, and calculation method. Diluted EPS is reported only; subtracting cumulative EPS would not be a safe general rule.

## What the final views mean

| View | Grain | Use |
| --- | --- | --- |
| `gold.fct_financial_metrics` | One company/accession/metric/exact selected or derived period | Exact `DECIMAL(38,18)` value, unit, origin, period, rule versions, and source lineage. |
| `gold.dim_filings` | One verified active CIK/accession | Filing/fiscal metadata, source identity, counts, missing critical metrics, and Gold quality status. |
| `gold.metric_quality_issues` | One upstream issue occurrence | Selection, accuracy, period, and derivation problems with severity and available evidence. Repeated issues remain separate. |

`dim_filings` includes active filings even if no metric is selected. Its five-metric critical profile requires reported revenue, net income, and diluted EPS for the current annual/quarter period, plus assets and liabilities at the report-date instant. It marks a filing `FAILED` when filing metadata or its report date is missing, the form is unsupported, or no metric was selected. Missing critical metrics or an error issue makes it `PARTIAL`; otherwise it is `COMPLETE`. Missing fiscal metadata may prevent a duration from qualifying, but it is not itself a hard-coded failure status. A `COMPLETE` filing can still have supplemental warnings. This quality status is **not** the pipeline execution status or the dbt result.

There are no fabricated zero-valued metric rows. Source values stay exact Decimals with their original unit expressions, and Gold does not convert currencies or sum segments to manufacture a consolidated total. The dashboard reads these final views without recalculating their financial rules.

The views also separate two kinds of “complete.” `dim_filings.gold_quality_status` checks the reported critical profile and error issues for one accession. `metric_quality_issues` can still contain supplemental warnings, such as a missing cumulative predecessor for cash flow. The end-to-end execution summary answers whether the stages ran and dbt built; it cannot stand in for either of those financial checks.

## Reproducibility and limits

dbt models and tests live under `transform/models/` and `transform/tests/`. Tests check source keys, grain, eligible selection, issue shape, period classification, exact calculations, and current/predecessor lineage. The concept map and rule-version columns make a result explainable when code changes; rerunning against the same active inputs produces the same logical rows.

The SQL is organized as staging views over Silver, intermediate candidates and selection/derivation models, then the three marts. The staging layer preserves Silver names and types; it does not make a financial choice. Intermediate models keep candidates and issue evidence available for review. Final marts publish only selected or supported derived rows, with tests checking that each occurrence and predecessor still belongs to the right active company and accession. This is why financial logic lives in dbt rather than a second Python transformation path.

The models retain accession history. They do not pick the latest amendment or restatement, reconcile different accounting policies between companies, or create full-year coverage from a bounded filing selection. The [Apple/Microsoft acceptance report](../validation/multi-company-acceptance-V1.md) shows actual rows, derivations, and missing-predecessor warnings from four selected filings.
