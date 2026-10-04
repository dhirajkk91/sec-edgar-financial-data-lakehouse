# Gold financial metrics V1

## What we are trying to build

Bronze keeps the SEC files we received. Silver turns the XBRL inside those files into verified facts that we can query. Gold is where those facts become financial information that someone can actually use.

An analyst should not need to know what an XBRL context is, why the same revenue concept appears six times, or which namespace Apple used in a particular year. They should be able to ask for revenue, net income, assets, or EPS and still trace the result back to the filing that reported it.

That is the purpose of this version of Gold. It gives common financial metrics consistent names, keeps their source evidence, and makes uncertainty visible instead of hiding it behind a convenient number.

The first few builds will use Apple because we already have real Apple filings in the lakehouse. The design is not Apple-specific, though. Concept mappings and selection rules are kept outside company-specific SQL so we can test the same process against other filers later.

## What we want to answer

The first useful questions are straightforward:

- What revenue, operating income, net income, and diluted EPS did the company report?
- What were assets, liabilities, stockholders’ equity, and cash at the end of the period?
- What operating cash flow and capital spending were reported?
- Is a value quarterly, year-to-date, annual, or an instant balance?
- Was it reported by the company or calculated by us?
- Which filing and Silver fact support it?
- Is anything missing or ambiguous?
- How did the value change from the comparable quarter or year?

These are the questions that will eventually feed the dashboard. The dashboard will display the answers, but it will not contain the accounting logic used to produce them.

## Where Gold sits

```text
+-------------------------------------------+
| Silver catalog                            |
| Active filings, facts and dimensions     |
+---------------------+---------------------+
                      |
                      | dbt reads verified Silver data
                      v
+-------------------------------------------+
| Candidate facts                           |
| Known concepts with their source context |
+---------------------+---------------------+
                      |
                      | selection and quality rules
                      v
+-------------------------------------------+
| Gold financial metrics                    |
| One business meaning for each metric     |
+---------------------+---------------------+
                      |
                      | SQL and dashboard queries
                      v
+-------------------------------------------+
| Trends, comparisons and quality notices  |
+-------------------------------------------+
```

Gold reads the active Silver relations in DuckDB. It does not scan old Silver versions on disk and does not change anything in Bronze or Silver. If a Gold build fails, the source layers are still intact and can be inspected independently.

DuckDB is the local database for this version. dbt manages the Gold SQL models, their order, documentation, and tests. Python remains responsible for SEC requests, file publication, XBRL extraction, and the Silver catalog. This split is deliberate. Financial transformations are easier to review as SQL than as another custom Python framework.

## Starting from Silver

The Gold project begins with four existing relations:

- `silver.active_filings`
- `silver.facts`
- `silver.fact_dimensions`
- `silver.rejected_facts`

We will put thin staging views in front of them. Those views give dbt a clear source contract and keep column selection explicit. They are not meant to rename every field or apply financial logic.

There is one known gap in the current catalog. `silver.active_filings` does not include the SEC form, filing date, or report date. We need those fields before we can reliably call a duration quarterly, year-to-date, or annual.

We will fix that as a separate increment. Until then, the first Gold model will show metric candidates without pretending it knows the final reporting period. Guessing a form from an accession number or deciding that a fact is quarterly because it is close to 90 days would make the model look finished while leaving an important assumption buried inside it.

The filing metadata we eventually bring into Gold should include:

- form, such as `10-K`, `10-Q`, `10-K/A`, or `10-Q/A`
- filing date
- report date
- primary document
- fiscal year and fiscal period focus when we can verify them
- whether the filing is an amendment

## The first working model

The first model will be `gold.int_financial_metric_candidates`.

Its grain is one row for each active Silver fact occurrence that matches a concept in our metric mapping. If one filing contains six possible revenue facts, the model keeps six rows. That is useful evidence, not a duplicate problem to hide.

Each candidate keeps the fields we need to understand it:

- metric code and concept priority
- CIK and accession number
- Silver occurrence ID
- source document and checksum
- concept namespace and local name
- raw and decimal values
- unit and accuracy attributes
- context and entity identity
- period boundaries
- number of dimensions
- source parser and schema versions

We will also calculate a few descriptive fields, such as inclusive duration days and whether the unit is USD. These help us inspect candidates, but they do not decide the final value by themselves.

The first model starts with revenue only. Once the candidate behavior is clear on real filings, we can reuse the same structure for the other metrics.

## Mapping XBRL concepts to business metrics

XBRL concepts change across taxonomy versions, and companies do not always use the same concept for the same business meaning. We will keep the mapping in a version-controlled dbt seed rather than spreading concept names across several SQL files.

The first revenue mapping recognizes these US-GAAP concepts in this order:

1. `RevenueFromContractWithCustomerExcludingAssessedTax`
2. `SalesRevenueNet`
3. `Revenues`

The order gives us a consistent preference when the surrounding evidence is otherwise equal. It does not mean that priority one automatically wins. A fact with the wrong period or a product dimension is not a better company revenue number just because its concept has a higher priority.

The planned metric set for this version is:

| Metric | Period shape | Importance |
| --- | --- | --- |
| Revenue | duration | critical |
| Operating income | duration | supplemental |
| Net income | duration | critical |
| Diluted EPS | duration | critical |
| Assets | instant | critical |
| Liabilities | instant | critical |
| Stockholders’ equity | instant | supplemental |
| Cash and cash equivalents | instant | supplemental |
| Operating cash flow | duration | supplemental |
| Capital expenditures | duration | supplemental |

We are not adding all of these at once. Revenue gives us a small working path from Silver to Gold, and the rest can follow once the selection rules have been exercised against actual filings.

Free cash flow is not included as a directly reported metric. If we add it later, it will be marked as derived and will point to the operating cash flow and capital expenditure values used in the calculation.

## Dimensions are part of the meaning

Apple’s revenue facts already show why dimensions matter. Product revenue, service revenue, geographic revenue, and total revenue can all use the same concept. Adding them together without understanding the axes could double-count the business.

The candidate model will summarize dimensions separately and join the count back to each fact. This avoids multiplying the fact rows when one context contains more than one dimension.

For the main company-level metrics, a dimensionless fact will usually be the strongest candidate. It is still not enough on its own. The concept, period, unit, entity, and filing metadata also need to line up.

We are not going to manufacture a total by summing segments unless a later metric-specific rule explains why that is safe. For this version, an unresolved dimension is a reason to keep the value as a candidate or record a quality issue.

## Periods are the difficult part

The same filing can contain several periods for the same concept. A 10-Q may include the current quarter, year-to-date, and comparable periods from the prior year. A 10-K may contain several annual comparisons. The balance sheet uses instant dates instead of durations.

Gold will eventually use these period labels:

- `quarter`
- `year_to_date`
- `annual`
- `instant`
- `derived_quarter`

For a 10-Q, revenue and other income-statement metrics should use the current quarter when the filing reports it. A longer year-to-date value stays year-to-date. This matters especially for cash flow, which is commonly reported year-to-date in a 10-Q.

For a 10-K, the annual value comes from the annual fact in that filing. We do not add a 10-K value to the three 10-Q values. The 10-K already covers the fiscal year.

Balance-sheet values use an instant context that matches the verified report date.

### Reported-period classification V1

`gold.int_reported_financial_periods` combines the four reported-metric views without changing their selected values, dates, units or occurrence evidence. It attaches fiscal metadata by CIK, accession, source document and checksum. Available fiscal metadata must also agree with the selected form, report date, metadata run ID and submissions checksum.

Duration classification requires resolved DEI fiscal year, fiscal focus and document end date. Both the reported period end and document end must match the verified report date. Start and end must form a valid duration, with no instant date. The rule uses inclusive days calculated from those boundaries, leaving the original `duration_days` unchanged. A PARTIAL extraction can still support classification when these fields resolved consistently; its status remains visible.

| Verified form and fiscal focus | Inclusive days | Classification |
| --- | --- | --- |
| 10-K or 10-K/A, FY | 350–380 | annual |
| 10-Q or 10-Q/A, Q1, Q2 or Q3 | 80–100 | quarter |
| 10-Q or 10-Q/A, Q2 | 170–200 | year_to_date |
| 10-Q or 10-Q/A, Q3 | 260–290 | year_to_date |

These V1 ranges accommodate common calendar periods and 52-/53-week reporting. Q1 produces one quarter row, even though the same value also represents the first cumulative quarter. Quarter and year-to-date rows can coexist for Q2 and Q3. A valid instant matching the report date needs no resolved fiscal focus or fiscal record, but conflicting available fiscal lineage still prevents classification.

Unusual periods and missing or inconsistent metadata remain as `unclassified`, with a stable issue code and explanation. When distinct otherwise eligible periods compete for the same classification within a company, accession and metric, every competing row remains unclassified with its boundaries and supporting IDs intact. No period is chosen by ranking.

This is a versioned classification policy, not proof of a company's fiscal calendar or a universal SEC period definition. The fiscal year comes from reported DEI, not the calendar year of a date. The DEI context start is not treated as a fiscal-year boundary. Derived quarters and fiscal-calendar verification remain separate work.

Later filings also repeat comparative values from older periods. Those remain useful evidence in Silver. For a clean time series, Gold will normally use the current period selected from each filing rather than publishing the same historical period again from every later filing.

### Quarterly cash flow from cumulative inputs

Cash-flow filings often report cumulative values instead of standalone quarters. `gold.int_derived_quarterly_cash_flow` uses Q2 year-to-date minus the previous Q1 reported quarter, or Q3 year-to-date minus the previous Q2 year-to-date value, for operating cash flow and capital expenditures. Q1 stays reported. A classified reported quarter in the current filing prevents a duplicate calculation.

The inputs must belong to the same company, metric and reported fiscal year, with the required fiscal roles and different accessions. Their cumulative starts must match exactly, and the predecessor must end earlier. Both must be valid dimensionless USD durations ending on their verified report dates. The derived period starts the day after the predecessor ends and must span 80–100 inclusive days. Document-scoped unit and context IDs need not match.

We count all predecessor filings before checking pair compatibility. Original and amended filings remain separate candidates; more than one blocks calculation even if values agree or only one pair is compatible. Missing inputs and failed checks produce supplemental warnings in `gold.int_quarterly_cash_flow_derivation_issues`. Each attempted target has one derived row or one issue. Unclassified inputs retain their existing classification issues.

The result is explicitly derived, with exact DECIMAL(38,18) subtraction and both input identities and ordered supporting-ID lists. Zero and negative results are retained, including the tagged capital-expenditure sign. Each input's decimals and precision remain independent; the calculation invents no derived accuracy attribute. Overflow produces an issue for that target without blocking other rows. These arithmetic and boundary checks do not independently prove identical accounting policies or resolve restatements. Annual and Q4 calculations remain separate work.

### The missing fourth quarter

There is usually no separate 10-Q for the fourth quarter. A standalone Q4 income-statement value may need to be calculated as the 10-K annual value minus the nine-month year-to-date value from the third-quarter 10-Q.

That calculation will come later. Before using it, we need to know that both values belong to the same company, fiscal year, metric, unit, and dimensional context. Neither source can be ambiguous.

A calculated Q4 row will be labeled `derived_quarter`, keep both input occurrence IDs, and show the calculation method. A dashboard user should always be able to tell the difference between a value the company reported and one the lakehouse derived.

## How a candidate becomes a selected metric

Selection will happen in stages rather than one large ranking expression.

First, the fact has to match a configured concept and the expected period shape. Then we check that the filing is active, the value is usable, and the entity and unit make sense. Filing metadata identifies the current reporting period. Dimensions tell us whether the value is consolidated or a breakdown.

Several occurrences may still remain after those checks.

If they agree on the metric, value, unit, period, entity, dimensions, and relevant accuracy attributes, we can treat them as equivalent. We will choose one deterministically for the final row while leaving every occurrence visible in the candidate model.

If one candidate has clearly stronger evidence, the selected row will store the reason. For example, a consolidated current-period value is stronger than a product-member value for company-wide revenue.

If equally credible candidates disagree, Gold stops there. It records a conflict instead of choosing whichever row happens to sort first.

## Amendments

An amended filing has its own accession number, so it remains separate from the original filing. We will not rewrite the original Gold rows when a `10-K/A` or `10-Q/A` arrives.

The company trend model will eventually need a clear rule for which accession is authoritative. That rule depends on verified form and filing dates and should be visible in the data. Until we add it, original and amended filings can coexist without one silently replacing the other.

## Gold tables

### `gold.dim_filings`

This table will have one row per accession number. It will hold the company identity, form, filing and report dates, fiscal-period metadata, active Silver version, amendment information, and the overall Gold quality status.

### `gold.fct_financial_metrics`

The final grain will be one row per filing, canonical metric, and selected financial period.

A row will include the value, unit, period type, period boundaries, concept, source occurrence ID, source document checksum, selection-rule version, and whether the value was reported or derived.

Only selected values belong here. A missing metric is not represented as zero, and an unresolved conflict is not forced into the fact table.

### `gold.metric_quality_issues`

This table will keep one row for each metric problem found in a filing. Examples include a missing critical metric, conflicting candidates, an unsupported unit, missing filing metadata, or a failed derived calculation.

Each issue will have a stable reason code, severity, explanation, filing identity, and any occurrence IDs that were involved.

### `gold.int_financial_metric_candidates`

This is the working evidence table. It is useful for debugging mappings and explaining why a value was selected or rejected, but it is not the dashboard contract.

## Filing quality

Not every missing metric has the same impact. The mapping will identify which metrics are critical.

For the first 10-K and 10-Q profile, revenue, net income, diluted EPS, assets, and liabilities are critical. If one of them is missing or still in conflict, the filing’s Gold result is `PARTIAL`.

A missing supplemental metric, such as operating income or cash, creates a warning but does not automatically make the whole filing partial. This follows the same principle we used earlier in the project: keep usable data moving while making the problem visible.

If the filing cannot be matched to verified metadata, or none of the supported metrics can be selected reliably, Gold marks it `FAILED` rather than publishing an empty-looking success.

## Lineage

Every final value should lead back to:

- the CIK and accession number
- the active Silver version
- the source document and SHA-256
- the Silver occurrence ID
- the XBRL concept, context, and unit
- the mapping version
- the selection-rule version

Derived values keep the lineage for every input. This lets us explain a number from the dashboard all the way back to the SEC filing without searching the original XML by hand.

## Refresh behavior

The normal local flow is simple:

```text
+--------------------------+
| Refresh Silver catalog   |
+------------+-------------+
             |
             v
+--------------------------+
| Check dbt sources        |
+------------+-------------+
             |
             v
+--------------------------+
| Build candidates         |
+------------+-------------+
             |
             v
+--------------------------+
| Select metrics and log   |
| quality issues           |
+------------+-------------+
             |
             v
+--------------------------+
| Test Gold relations      |
+--------------------------+
```

Rebuilding with the same active Silver data and the same rules should produce the same logical rows. Gold identity will not depend on row order or a random processing ID.

When mapping or selection logic changes, the rule version gives us a way to explain why the output changed even though the SEC filing did not.

## Testing approach

The dbt tests will cover the shape of the data and the financial rules.

At the structural level, we will check identifiers, source relationships, candidate uniqueness, stable types, mapping uniqueness, and joins that could multiply facts.

At the business level, we will check that only configured concepts enter the candidate model, period kinds agree with their mappings, final metric grain is unique, derived values keep their inputs, and conflicts become quality issues rather than arbitrary selections.

The automated tests should not depend on Apple always producing a fixed number of rows. Apple is our real acceptance case. Small controlled fixtures will cover edge cases such as duplicate facts, conflicting values, missing dimensions, and incomplete metadata.

## What belongs in this version

Gold V1 covers the local dbt project, Silver sources, staging views, metric mappings, candidate facts, verified filing metadata, selected metrics, quality issues, and lineage. The result should be queryable from DuckDB and stable enough for a dashboard to use.

It does not try to support every US-GAAP concept or every company extension. It does not convert currencies, forecast results, or normalize different accounting policies across companies. It also does not include cloud deployment, scheduling, concurrent writers, or the dashboard itself.

Those are worthwhile later steps, but adding them now would make it harder to tell whether the financial metric logic is actually correct.

## Build order

We will build this in a few visible increments:

1. Add the dbt project and expose revenue candidates from active Silver facts.
2. Bring verified filing metadata into the model.
3. Select reported revenue and record conflicts.
4. Add the remaining critical income-statement and balance-sheet metrics.
5. Add the supplemental cash-flow metrics.
6. Add comparisons and a clearly labeled Q4 calculation.
7. Publish the final Gold relations for dashboard work.

The first increment is deliberately a candidate model. It lets us inspect real values and dimensions before we encode a selection rule that would be much harder to unwind later.

## When V1 is complete

We will consider this version complete when dbt can build and test the Gold models against the local DuckDB catalog, filing metadata is verified, the supported mappings are version controlled, and every selected value has deterministic selection evidence.

Conflicts and missing critical metrics should be visible as quality issues. Quarterly, year-to-date, annual, instant, and derived periods should remain distinct. Rerunning the same inputs should not create duplicate business rows.

Most importantly, a SQL query should be able to produce a defensible financial trend while still showing exactly where each number came from.
