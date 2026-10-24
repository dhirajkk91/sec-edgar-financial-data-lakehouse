# 20-company cohort validation

**Result:** all 400 planned filings landed in verified Bronze, active Silver and complete fiscal metadata, and the Gold build passes all 571 dbt nodes. The cohort's own status stays `PARTIAL` for one reason: the SEC listing files behind three JPMorgan filings changed between planning and processing (see [JPMorgan source trace](#jpmorgan-source-trace)). The filings themselves are correct.

| Measure | Result |
| --- | ---: |
| Companies / filings | 20 / 400 |
| Accepted XBRL facts / rejected | 601,103 / 0 |
| Dimension rows | 643,624 |
| Gold financial values | 4,310 (3,576 reported, 734 derived) |
| Filings with `COMPLETE` Gold quality | 238 of 400 |
| Quality issues (errors / warnings) | 557 (240 / 317) |
| Company-period rows in `fct_company_financials` | 500 |
| dbt nodes passing | 571 of 571 |
| Full Gold rebuild | about 18 seconds |

## Scope

### Companies

The [manifest](../../config/cohort-sp500-top20-2026-10-15.json) fixes the ranking and filing cutoff at **October 15, 2026**. [FinHacker's S&P 500 top-20 list](https://www.finhacker.cz/en/top-20-sp-500-companies-by-market-cap/) supplied the full market-cap ranking, and the [iShares IVV holdings file](https://www.ishares.com/us/products/239726/ishares-core-s-p-500-etf/latest-holdings.csv) cross-checked membership and share classes. IVV weights by float-adjusted value, so its order differs from the full-cap ranking. Alphabet's GOOGL and GOOG share one CIK and count once. The market caps are the source's rounded figures, retrieved October 16, 2026, not independent valuations. Each CIK was checked against live SEC metadata when the plan was made.

| Rank | Issuer | Ticker(s) | CIK | Displayed market cap |
| ---: | --- | --- | --- | ---: |
| 1 | NVIDIA | NVDA | `0001045810` | 5.245T |
| 2 | Apple | AAPL | `0000320193` | 4.996T |
| 3 | Alphabet | GOOGL, GOOG | `0001652044` | 4.271T |
| 4 | Microsoft | MSFT | `0000789019` | 4.006T |
| 5 | Amazon | AMZN | `0001018724` | 2.747T |
| 6 | Meta Platforms | META | `0001326801` | 1.877T |
| 7 | Broadcom | AVGO | `0001730168` | 1.644T |
| 8 | Tesla | TSLA | `0001318605` | 1.431T |
| 9 | Eli Lilly | LLY | `0000059478` | 1.178T |
| 10 | Berkshire Hathaway | BRK.B | `0001067983` | 1.129T |
| 11 | Micron Technology | MU | `0000723125` | 1.053T |
| 12 | Walmart | WMT | `0000104169` | 936.12B |
| 13 | Advanced Micro Devices | AMD | `0000002488` | 917.56B |
| 14 | JPMorgan Chase | JPM | `0000019617` | 891.03B |
| 15 | Visa | V | `0001403161` | 746.85B |
| 16 | Exxon Mobil | XOM | `0000034088` | 718.28B |
| 17 | Johnson & Johnson | JNJ | `0000200406` | 609.96B |
| 18 | Mastercard | MA | `0001141391` | 541.13B |
| 19 | AbbVie | ABBV | `0001551152` | 500.79B |
| 20 | Palantir Technologies | PLTR | `0001321655` | 481.78B |

### Selection

The planner used exact `10-K` and `10-Q` forms filed by the cutoff. It took the five latest original annual **report dates**, then the original 10-Q report dates between each annual period and the one before it, which correctly captures an annual report filed after its fiscal year ended. Amendments were excluded. After ingestion, each filing's own DEI fiscal year, quarter and period end were compared with the plan; a report-date interval is a candidate, not a substitute for fiscal evidence.

That gave **400 filings**, 20 per company, across 100 annual periods. Alphabet, Meta, Eli Lilly, Walmart and JPMorgan needed older SEC listing files to reach five years: 9, 13, 3, 8 and 18 of their filings came from those files. Apple's 20 were all in `filings.recent`. The saved listings, their hashes and URLs are under `plan-sources/`, and `cohort-plan.json` has the filing-level selection. Exxon's SEC ticker list was empty when checked; its CIK and name matched.

## Results

### Coverage

Every company has all 20 planned filings with complete Bronze and Silver processing and complete fiscal metadata. Year-end dates vary: Johnson & Johnson's FY2022, for example, ended on January 1, 2023.

| Issuer | Fiscal years | Year ends | Filings landed | Latest run ID | Company result |
| --- | --- | --- | :---: | --- | --- |
| NVIDIA | FY2022–FY2026 | late January | 20 / 20 | `cohort_604d3d2c5f4149d3a77dde447b2717c1` | COMPLETE |
| Apple | FY2021–FY2025 | late September | 20 / 20 | `cohort_02604c6e962f45a89a9aea061bddedc3` | COMPLETE |
| Alphabet | FY2021–FY2025 | December 31 | 20 / 20 | `cohort_307b98a1f66e4e40b5a955d26bf19313` | COMPLETE |
| Microsoft | FY2022–FY2026 | June 30 | 20 / 20 | `cohort_c5914f14082f46c78b153c43699b455d` | COMPLETE |
| Amazon | FY2021–FY2025 | December 31 | 20 / 20 | `cohort_b5886d7965014f079f86c8276a32f83b` | COMPLETE |
| Meta Platforms | FY2021–FY2025 | December 31 | 20 / 20 | `cohort_01b1e334619b4830bff5e15f28530fe2` | COMPLETE |
| Broadcom | FY2021–FY2025 | late October or early November | 20 / 20 | `cohort_60c1aebe04e448e880e7ff582140339f` | COMPLETE |
| Tesla | FY2021–FY2025 | December 31 | 20 / 20 | `cohort_711543d6e0f4458fba6eb9abc79911f6` | COMPLETE |
| Eli Lilly | FY2021–FY2025 | December 31 | 20 / 20 | `cohort_8a4fa32006bb4593a4bf176c47f5b7cc` | COMPLETE |
| Berkshire Hathaway | FY2021–FY2025 | December 31 | 20 / 20 | `cohort_f47306f8ab2f4035be6e51a0a14f50b1` | COMPLETE |
| Micron Technology | FY2022–FY2026 | late August or early September | 20 / 20 | `cohort_dc77b81d864d4cecba25d717c189f09c` | COMPLETE |
| Walmart | FY2022–FY2026 | January 31 | 20 / 20 | `cohort_529d16ac5e8647f59053cb9241a918be` | COMPLETE |
| Advanced Micro Devices | FY2021–FY2025 | last Saturday of December | 20 / 20 | `cohort_820c8a8d922a4897ae304aedae5d7a37` | COMPLETE |
| JPMorgan Chase | FY2021–FY2025 | December 31 | 20 / 20 | `cohort_fbc83f78445046559c0776f03e9bdfb9` | PARTIAL |
| Visa | FY2021–FY2025 | September 30 | 20 / 20 | `cohort_68ef206875454556a80e2b04149a2bca` | COMPLETE |
| Exxon Mobil | FY2021–FY2025 | December 31 | 20 / 20 | `cohort_dd3430bb6bcd45bca8a4b98ec97378d1` | COMPLETE |
| Johnson & Johnson | FY2021–FY2025 | late December or early January (52/53-week year) | 20 / 20 | `cohort_59c580f4ea5c4929b266d6cc83396787` | COMPLETE |
| Mastercard | FY2021–FY2025 | December 31 | 20 / 20 | `cohort_11789849c77546eab6eacdb137868110` | COMPLETE |
| AbbVie | FY2021–FY2025 | December 31 | 20 / 20 | `cohort_c4d6eb336836432aa3081fdf21a379bb` | COMPLETE |
| Palantir Technologies | FY2021–FY2025 | December 31 | 20 / 20 | `cohort_53440729375e4bc889cc4326bd7e6e68` | COMPLETE |

The latest attempts processed 341 filings new and reused 59 already-verified ones, with none failed or left unattempted. Each of the 400 filings appears exactly once in the active Silver catalog, filing metadata, fiscal metadata and `gold.dim_filings`. Earlier attempts, such as the Apple and Alphabet pilot and Palantir's first attempt, are kept in `cohort-state.json`.

### Data volumes and Gold quality per company

| Issuer | Silver facts | Dimension rows | Rejected | Gold values | Quality issues | Complete filings |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| NVIDIA | 19,050 | 10,159 | 0 | 217 | 8 | 20 / 20 |
| Apple | 15,505 | 11,806 | 0 | 230 | 0 | 20 / 20 |
| Alphabet | 26,821 | 22,822 | 0 | 230 | 0 | 20 / 20 |
| Microsoft | 26,919 | 23,664 | 0 | 230 | 0 | 20 / 20 |
| Amazon | 20,448 | 10,968 | 0 | 210 | 65 | 0 / 20 |
| Meta Platforms | 17,642 | 10,662 | 0 | 230 | 0 | 20 / 20 |
| Broadcom | 22,818 | 14,429 | 0 | 230 | 0 | 20 / 20 |
| Tesla | 23,401 | 15,539 | 0 | 230 | 30 | 20 / 20 |
| Eli Lilly | 28,512 | 38,490 | 0 | 210 | 20 | 0 / 20 |
| Berkshire Hathaway | 39,197 | 56,555 | 0 | 174 | 106 | 0 / 20 |
| Micron Technology | 22,091 | 19,318 | 0 | 230 | 0 | 20 / 20 |
| Walmart | 17,092 | 10,702 | 0 | 205 | 85 | 0 / 20 |
| Advanced Micro Devices | 17,498 | 13,717 | 0 | 210 | 20 | 0 / 20 |
| JPMorgan Chase | 140,664 | 195,620 | 0 | 145 | 60 | 0 / 20 |
| Visa | 24,046 | 19,816 | 0 | 200 | 20 | 0 / 20 |
| Exxon Mobil | 24,879 | 24,848 | 0 | 230 | 44 | 20 / 20 |
| Johnson & Johnson | 43,849 | 77,137 | 0 | 230 | 3 | 19 / 20 |
| Mastercard | 23,687 | 17,701 | 0 | 230 | 11 | 20 / 20 |
| AbbVie | 31,285 | 38,868 | 0 | 210 | 65 | 0 / 20 |
| Palantir Technologies | 15,699 | 10,803 | 0 | 229 | 20 | 19 / 20 |

JPMorgan's filings are by far the largest, with 140,664 facts. A company with 0 complete filings has a missing required metric or an error issue in every filing, and the reason is always in the issue table: JPMorgan's capex, Berkshire's and Visa's EPS (reported per share class), and so on.

| Issue code | Severity | Count |
| --- | --- | ---: |
| `LOWER_PRIORITY_CONCEPT_DIFFERS` | Warning | 254 |
| `NO_ELIGIBLE_METRIC` | Error | 186 |
| `NO_ELIGIBLE_METRIC` | Warning | 27 |
| `UNSUPPORTED_DURATION` | Error | 33 |
| `UNSUPPORTED_DURATION` | Warning | 30 |
| `NO_ELIGIBLE_REVENUE` | Error | 15 |
| `MISSING_PREVIOUS_CUMULATIVE` | Warning | 6 |
| `METRIC_VALUE_CONFLICT` | Error | 5 |
| `METRIC_ACCURACY_UNRESOLVED` | Error | 1 |

Severity depends on whether the metric is required. Issue rows describe individual metric choices, not failed filings; what each code means is in the [Gold doc](../architectures/gold-financial-metrics.md#quality-issues).

### Lineage audit

The audit joined every selected Gold occurrence ID to its accepted Silver fact and compared the company, accession, source document, checksum and exact value: 8,406 metric-support references and 385 issue-support references, with no mismatch. Every derived value's predecessor came from the same company, and every result equalled the exact difference of its two stored inputs. Those counts are from the audit of the first full build; later changes were checked value by value (see [Concept fallbacks](#concept-fallbacks)) and by the dbt lineage tests on every build.

Sample rows, with the stored fixed scale shown so they can be compared directly with the full records. All six still match the current Gold tables exactly:

| Issuer and fiscal focus | Accession / metric | Period and value | Source lineage |
| --- | --- | --- | --- |
| Apple FY 2021 | `0000320193-21-000105` / revenue | Reported annual, 2020-09-27–2021-09-25: `365817000000.000000000000000000` USD | `8a4e7deb719f9c4ddc7a156ee5af9e7ce4e1f447fdae235414d71f6d0407795c` |
| AMD FY 2021 | `0000002488-22-000016` / diluted EPS | Reported annual: `2.570000000000000000` USD/share | `0630b99139091e3ebbc60d81cb8da753fb2902c274fb76d6e3fffb1f28766cee` |
| AMD FY 2021 Q1 | `0000002488-21-000056` / net income | Reported quarter, 2020-12-27–2021-03-27: `555000000.000000000000000000` USD | `77bf39b15690ca08e407625be944aba2dd5f661440ee044b541c4aaf05d9d129` |
| Alphabet FY 2021 Q2 | `0001652044-21-000047` / operating cash flow | Reported year-to-date, 2021-01-01–2021-06-30: `41179000000.000000000000000000` USD | `f72c8a4149be9f4cee093de6b098c45d0403c7f0cae989bfb28d5ea7a8bb9396` |
| Alphabet FY 2021 Q1 | `0001652044-21-000020` / total assets | Reported instant, 2021-03-31: `327095000000.000000000000000000` USD | `2135968bc8ac7f5a66563f732dcf73b08f5097ac4d4b97bb3df0105f16092910` |
| Apple FY 2021 Q2 | `0000320193-21-000056` / operating cash flow | Derived quarter: `62744000000.000000000000000000` − `38763000000.000000000000000000` = `23981000000.000000000000000000` USD | Current `91d05534fa4a1114ae7c4d114ff74825ff04540e3c1e19cf86f0b888da28aa29`; predecessor `0000320193-21-000010` / `402492f1eae66ade8f6436cead7120ba7146856ce51aa4fdd38bb275616c2a5d` |

### Company tables

| Check | Result |
| --- | --- |
| Company profiles | 20, loaded from 25 saved run records without new SEC requests (21 new snapshots, 4 identical responses reported as `ALREADY_EXISTS`) |
| Sectors (SEC office) | Technology 5, Manufacturing 5, Trade & Services 4, Life Sciences 3, Finance 2, Energy & Transportation 1 |
| Empty SEC fields | Exxon's ticker list was empty, so its `primary_ticker` is null |
| `fct_company_financials` | 500 rows: 20 companies × 5 fiscal years × FY, Q1, Q2, Q3, Q4 |
| Revenue present | FY 100, Q1–Q4 95 each, of 100 |
| Net income present | 100 of 100 in every period |
| Free cash flow present | 93 of 100 in every period |
| Q4 diluted EPS | 0 of 100, as intended: EPS is never derived |
| Spot check | Apple FY2025: revenue 416,161,000,000; free cash flow 111,482,000,000 − 12,715,000,000 = 98,767,000,000 USD |

Gaps in this table are the same gaps already recorded in `gold.metric_quality_issues`; nothing fills them.

## JPMorgan source trace

One JPMorgan filing, `0000019617-24-000326`, fell outside the date range its SEC history file declares for itself. Two more, `0000019617-25-000270` and `0000019617-24-000611`, sat in history files `-007` and `-010` when the plan was saved and in `-008` and `-011` when they were processed. Their form, filing date, report date and primary document didn't change, and the later listing bytes are verified in the company run. But the frozen source trace no longer matches, so the runner keeps JPMorgan, and therefore the cohort, `PARTIAL` instead of hiding the difference. Resolving it needs a manual comparison of `cohort-plan.json` with the later saved listings; a plain `resume` leaves the warning in place unless the evidence changes.

## Changes validated on this dataset

Each change below was made and checked against the same 400 filings, without new SEC requests unless stated.

### Fiscal namespaces

The Apple and Alphabet pilot left two Alphabet filings with partial fiscal metadata: the extractor didn't recognize the SEC's `dei/2021q4` namespace. It now also recognizes `dei/2021q4` and `dei/2022q4`, as listed in the [SEC DEI taxonomy index](https://xbrl.sec.gov/dei/). The earlier rows stay as evidence and Gold uses the newest extraction for the active document. The corrected pilot had 20 complete fiscal rows and 230 financial values per company, with no Gold quality issues, and a later pilot rerun reused all 40 filings, returned `ALREADY_EXISTS` for 40 fiscal rows and left the complete Gold records unchanged.

### Palantir's XBRL label

Palantir's FY2021 Q1 filing, `0001193125-21-159222`, labels its XML `XBRL INSTANCE DOCUMENT`, while the readiness check accepted only `EXTRACTED XBRL INSTANCE DOCUMENT`. Bronze had saved all 14 files correctly but marked the filing not parser-ready. The check now accepts either **exact** label and still rejects zero or several candidates. A read-only extraction of the saved XML produced 481 accepted facts and no rejections. The original publication is archived under `archived-bronze-policy/`; a normal rerun published a new verified filing, reused Palantir's other 19 filings and loaded the missing fiscal metadata. No source bytes were edited.

### Dashboard memory

The first dashboard run on the full cohort exhausted DuckDB's default memory while rereading Gold after a filing selection. The reader now uses one thread and a 4 GB limit, the same budget as the batch build. With that, the inspector completed on the full cohort and the browser showed Apple's FY2025 10-K: `COMPLETE`, 12 financial values, no issues, and separate reported annual (416,161,000,000) and derived Q4 (102,466,000,000) revenue.

### Gold stored as tables

With every Gold model as a view, each read recomputed the whole chain and the dashboard still ran out of memory. Intermediate models and marts became tables, with staging left as views over Silver:

| Build | dbt nodes | Time |
| --- | ---: | ---: |
| All models as views | 562 | 34m27s |
| Intermediate models and marts as tables | 562 | 18.07s (22.5s for the whole command) |

Both builds passed every node, on the same data.

### Concept fallbacks

The Companies view showed gaps for large companies, traced to two causes: concepts that weren't mapped (Amazon, NVIDIA and Visa report capex as `PaymentsToAcquireProductiveAssets`, Eli Lilly as `PaymentsToAcquireOtherPropertyPlantAndEquipment`, Broadcom and Mastercard report net income as `ProfitLoss`), and revenue concepts that measure different things (Walmart's FY2026 `Revenues` of 713,163,000,000 against contract revenue of 706,413,000,000; Berkshire's 371,444,000,000 against 247,244,000,000). Requiring every mapped concept to agree had withheld Walmart's and Berkshire's revenue in all five years.

The fix added the missing concepts as fallbacks, put total `Revenues` first, and changed selection so that the highest-priority concept supplies the value, its own occurrences must agree, every agreeing occurrence of any concept stays as evidence, and a differing lower-priority concept becomes a `LOWER_PRIORITY_CONCEPT_DIFFERS` warning. Every Gold value was compared with a snapshot taken before the change:

| Check | Before | After |
| --- | ---: | ---: |
| `fct_financial_metrics` rows | 4,015 | 4,310 |
| Values gained / changed / lost | — | 295 / 0 / 0 |
| Same value, now represented by `Revenues` instead of contract revenue | — | 24 |
| Filings with `COMPLETE` Gold quality | 190 | 238 |
| Issue rows (errors / warnings) | 487 (364 / 123) | 557 (240 / 317) |
| Annual rows with revenue (of 100) | 88 | 100 |
| Annual rows with net income | 91 | 100 |
| Annual rows with capex and free cash flow | 75 | 93 |
| Quarterly rows with free cash flow (of 400) | 300 | 372 |

The 295 new values are revenue, net income and capex for Walmart, Berkshire, Mastercard, Broadcom, Amazon, Visa, Eli Lilly, NVIDIA and Exxon. **No existing value changed or disappeared.** The 24 same-value rows are filings where both revenue concepts agree; both stay in the evidence.

The issue mix changed as expected: 85 revenue conflicts disappeared, `NO_ELIGIBLE_METRIC` fell from 325 to 213, and 254 `LOWER_PRIORITY_CONCEPT_DIFFERS` warnings appeared: 169 for net income, where `ProfitLoss` includes noncontrolling interests (Exxon, Tesla, AbbVie, Berkshire, Walmart, Palantir), and 85 for revenue (Walmart, Berkshire, Exxon, Mastercard). `UNSUPPORTED_DURATION` warnings rose from 15 to 30 because the new concepts brought unusual periods, which are flagged rather than used. Some gaps stay on purpose: JPMorgan's capex, since a bank doesn't report it in a way that fits, and Berkshire's and Visa's EPS, reported per share class. The rebuilt Gold passed all 571 dbt nodes, and the CI build gave the same synthetic results as before.

## Evidence

Everything below is under the cohort's ignored data root:

| File | What it records |
| --- | --- |
| `cohort-plan.json`, `plan-sources/` | The frozen selection and the original SEC listing bytes |
| `cohort-state.json` | Every company attempt and dbt attempt |
| `runs/cik=<CIK>/run_id=<id>/run.json` | Each company run's outcomes and saved listings |
| `cohort-audit.json`, `cohort-audit.log` | The lineage audit and complete Gold samples |
| `quality-audit.log` | Quality-status, severity and issue-code counts |
| `full-cohort-run.log`, `palantir-recovery-and-build.log` | Company attempts and batch builds |
| `dashboard-inspect-after-resource-fix.log`, `dashboard-smoke.txt` | The read-only dashboard checks |
| `dbt/artifacts-<id>/` | Logs, `manifest.json` and `run_results.json` for every build |

The main `data/query/sec_edgar.duckdb` had the same SHA-256 before and after all cohort work: `095684c1c2572d6e202b44b262324854b813f07d61e8e9a5751b4d1c0284b52b`.

## Reproduce it

Keep the dashboard and other readers closed while a run writes, and use a fresh data root for every new attempt. With `SEC_USER_AGENT` set:

```sh
uv sync --locked --group dbt --group dashboard
cohort_root=$(mktemp -d data/cohort.XXXXXX)
manifest=config/cohort-sp500-top20-2026-10-15.json
uv run --no-sync sec-edgar cohort plan --manifest "$manifest" --data-root "$cohort_root"
uv run --no-sync sec-edgar cohort execute --manifest "$manifest" --data-root "$cohort_root" --dbt-timeout-seconds 43200
uv run --no-sync sec-edgar inspect --database "$cohort_root/query/sec_edgar.duckdb"
```

The [pipeline guide](../guides/running-the-pipeline.md) covers resuming, rebuilding Gold and recovery. A new run plans against the SEC listings as they are at that time; if they have changed, its results can differ from this record.
