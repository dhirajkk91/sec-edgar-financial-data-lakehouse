# Five-year local cohort: V1 acceptance

The local cohort contains five completed fiscal years of original annual and quarterly filings for 20 issuers in one Bronze, Silver, DuckDB, and Gold dataset. The frozen selection and per-accession evidence are under the ignored local root `data/cohort-v1-dc5b138b-e4da-4419-97d2-f1546ff6bf1d/`. That root is separate from `data/query/sec_edgar.duckdb`; the SEC contact was supplied through an environment variable.

**V1 result: PARTIAL.** All 400 selected filings reached verified Bronze, active Silver, and complete fiscal metadata; the final shared Gold build passed all 538 dbt nodes. JPMorgan's saved SEC history listings disagreed with the planning listings for three accessions, leaving both its company result and the cohort result `PARTIAL`. The filings landed, but their listing provenance needs review. Separately, Gold marks 210 of 400 filing summaries `PARTIAL` for financial selection or period-quality reasons; those are visible in the issue mart and do not change the dbt execution result.

## Which 20 companies

The [manifest](../../config/cohort-sp500-top20-2026-10-15.json) fixes the ranking and filing cutoff at **October 15, 2026**. [FinHacker's S&P 500 top-20 list](https://www.finhacker.cz/en/top-20-sp-500-companies-by-market-cap/) supplied the displayed full market-cap ranking; the [iShares IVV holdings file](https://www.ishares.com/us/products/239726/ishares-core-s-p-500-etf/latest-holdings.csv) cross-checked membership and share classes. These are different measures: IVV weights use float-adjusted market value, so their order is not the manifest's full-cap order. Alphabet's GOOGL and GOOG shares have one CIK and count once. The numbers below are the source's rounded display figures, not independently calculated valuations. Both sources were retrieved October 16, 2026. Each CIK was checked against live SEC submissions metadata when the plan was made.

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

The planner used exact `10-K` and `10-Q` forms filed by the cutoff. It took the five latest original annual **report dates**, then found original 10-Q report dates between each annual end and the preceding annual end. This captures an annual filing made after its fiscal year ended. The saved plan lists each accession, form, filing date, report date, primary document, and the SEC listing that supplied it before the filing is ingested; amendments are listed separately and excluded. Verified XBRL DEI fiscal year, quarter, and period end are compared with the plan after ingestion. A report-date interval is a candidate boundary, not a substitute for fiscal evidence.

The plan contains **400 selected accessions**, 20 per issuer, across 100 annual report-date periods. Alphabet, Meta, Eli Lilly, Walmart, and JPMorgan needed older CIK-specific submissions files to reach the full interval; 9, 13, 3, 8, and 18 selected accessions respectively came from those files. Apple's 20 were still in `filings.recent`. The complete saved source listings, hashes, and URLs are under `plan-sources/`; `cohort-plan.json` has the accession-level selection. The SEC's history-file date ranges disagree with some of their own rows. One selected JPMorgan accession, `0000019617-24-000326`, is among those disagreements. The plan preserves the row and marks this as a source-evidence coverage warning rather than silently changing its date or selecting a different filing. Exxon Mobil's SEC submissions ticker array was empty when checked; its CIK and entity name matched, but ticker identity could not be independently confirmed there.

## Filing coverage

The matrix below uses the **verified** annual DEI fiscal focus, the annual report end from the frozen plan, and the saved result for each original 10-K and Q1–Q3 10-Q. `✓` means the planned accession has complete Bronze/Silver processing and complete fiscal metadata. The full accessions, filing dates, report dates, and source-listing names are in `cohort-plan.json` and the corresponding company attempt in `cohort-state.json`. An annual end can fall in the next calendar year: Johnson & Johnson FY 2022 ended January 1, 2023.

| Issuer | Fiscal focus / annual report end | 10-K | Q1 | Q2 | Q3 |
| --- | --- | :---: | :---: | :---: | :---: |
| NVIDIA | FY 2026 / 2026-01-25 | ✓ | ✓ | ✓ | ✓ |
| NVIDIA | FY 2025 / 2025-01-26 | ✓ | ✓ | ✓ | ✓ |
| NVIDIA | FY 2024 / 2024-01-28 | ✓ | ✓ | ✓ | ✓ |
| NVIDIA | FY 2023 / 2023-01-29 | ✓ | ✓ | ✓ | ✓ |
| NVIDIA | FY 2022 / 2022-01-30 | ✓ | ✓ | ✓ | ✓ |
| Apple | FY 2025 / 2025-09-27 | ✓ | ✓ | ✓ | ✓ |
| Apple | FY 2024 / 2024-09-28 | ✓ | ✓ | ✓ | ✓ |
| Apple | FY 2023 / 2023-09-30 | ✓ | ✓ | ✓ | ✓ |
| Apple | FY 2022 / 2022-09-24 | ✓ | ✓ | ✓ | ✓ |
| Apple | FY 2021 / 2021-09-25 | ✓ | ✓ | ✓ | ✓ |
| Alphabet | FY 2025 / 2025-12-31 | ✓ | ✓ | ✓ | ✓ |
| Alphabet | FY 2024 / 2024-12-31 | ✓ | ✓ | ✓ | ✓ |
| Alphabet | FY 2023 / 2023-12-31 | ✓ | ✓ | ✓ | ✓ |
| Alphabet | FY 2022 / 2022-12-31 | ✓ | ✓ | ✓ | ✓ |
| Alphabet | FY 2021 / 2021-12-31 | ✓ | ✓ | ✓ | ✓ |
| Microsoft | FY 2026 / 2026-06-30 | ✓ | ✓ | ✓ | ✓ |
| Microsoft | FY 2025 / 2025-06-30 | ✓ | ✓ | ✓ | ✓ |
| Microsoft | FY 2024 / 2024-06-30 | ✓ | ✓ | ✓ | ✓ |
| Microsoft | FY 2023 / 2023-06-30 | ✓ | ✓ | ✓ | ✓ |
| Microsoft | FY 2022 / 2022-06-30 | ✓ | ✓ | ✓ | ✓ |
| Amazon | FY 2025 / 2025-12-31 | ✓ | ✓ | ✓ | ✓ |
| Amazon | FY 2024 / 2024-12-31 | ✓ | ✓ | ✓ | ✓ |
| Amazon | FY 2023 / 2023-12-31 | ✓ | ✓ | ✓ | ✓ |
| Amazon | FY 2022 / 2022-12-31 | ✓ | ✓ | ✓ | ✓ |
| Amazon | FY 2021 / 2021-12-31 | ✓ | ✓ | ✓ | ✓ |
| Meta Platforms | FY 2025 / 2025-12-31 | ✓ | ✓ | ✓ | ✓ |
| Meta Platforms | FY 2024 / 2024-12-31 | ✓ | ✓ | ✓ | ✓ |
| Meta Platforms | FY 2023 / 2023-12-31 | ✓ | ✓ | ✓ | ✓ |
| Meta Platforms | FY 2022 / 2022-12-31 | ✓ | ✓ | ✓ | ✓ |
| Meta Platforms | FY 2021 / 2021-12-31 | ✓ | ✓ | ✓ | ✓ |
| Broadcom | FY 2025 / 2025-11-02 | ✓ | ✓ | ✓ | ✓ |
| Broadcom | FY 2024 / 2024-11-03 | ✓ | ✓ | ✓ | ✓ |
| Broadcom | FY 2023 / 2023-10-29 | ✓ | ✓ | ✓ | ✓ |
| Broadcom | FY 2022 / 2022-10-30 | ✓ | ✓ | ✓ | ✓ |
| Broadcom | FY 2021 / 2021-10-31 | ✓ | ✓ | ✓ | ✓ |
| Tesla | FY 2025 / 2025-12-31 | ✓ | ✓ | ✓ | ✓ |
| Tesla | FY 2024 / 2024-12-31 | ✓ | ✓ | ✓ | ✓ |
| Tesla | FY 2023 / 2023-12-31 | ✓ | ✓ | ✓ | ✓ |
| Tesla | FY 2022 / 2022-12-31 | ✓ | ✓ | ✓ | ✓ |
| Tesla | FY 2021 / 2021-12-31 | ✓ | ✓ | ✓ | ✓ |
| Eli Lilly | FY 2025 / 2025-12-31 | ✓ | ✓ | ✓ | ✓ |
| Eli Lilly | FY 2024 / 2024-12-31 | ✓ | ✓ | ✓ | ✓ |
| Eli Lilly | FY 2023 / 2023-12-31 | ✓ | ✓ | ✓ | ✓ |
| Eli Lilly | FY 2022 / 2022-12-31 | ✓ | ✓ | ✓ | ✓ |
| Eli Lilly | FY 2021 / 2021-12-31 | ✓ | ✓ | ✓ | ✓ |
| Berkshire Hathaway | FY 2025 / 2025-12-31 | ✓ | ✓ | ✓ | ✓ |
| Berkshire Hathaway | FY 2024 / 2024-12-31 | ✓ | ✓ | ✓ | ✓ |
| Berkshire Hathaway | FY 2023 / 2023-12-31 | ✓ | ✓ | ✓ | ✓ |
| Berkshire Hathaway | FY 2022 / 2022-12-31 | ✓ | ✓ | ✓ | ✓ |
| Berkshire Hathaway | FY 2021 / 2021-12-31 | ✓ | ✓ | ✓ | ✓ |
| Micron Technology | FY 2026 / 2026-09-03 | ✓ | ✓ | ✓ | ✓ |
| Micron Technology | FY 2025 / 2025-08-28 | ✓ | ✓ | ✓ | ✓ |
| Micron Technology | FY 2024 / 2024-08-29 | ✓ | ✓ | ✓ | ✓ |
| Micron Technology | FY 2023 / 2023-08-31 | ✓ | ✓ | ✓ | ✓ |
| Micron Technology | FY 2022 / 2022-09-01 | ✓ | ✓ | ✓ | ✓ |
| Walmart | FY 2026 / 2026-01-31 | ✓ | ✓ | ✓ | ✓ |
| Walmart | FY 2025 / 2025-01-31 | ✓ | ✓ | ✓ | ✓ |
| Walmart | FY 2024 / 2024-01-31 | ✓ | ✓ | ✓ | ✓ |
| Walmart | FY 2023 / 2023-01-31 | ✓ | ✓ | ✓ | ✓ |
| Walmart | FY 2022 / 2022-01-31 | ✓ | ✓ | ✓ | ✓ |
| Advanced Micro Devices | FY 2025 / 2025-12-27 | ✓ | ✓ | ✓ | ✓ |
| Advanced Micro Devices | FY 2024 / 2024-12-28 | ✓ | ✓ | ✓ | ✓ |
| Advanced Micro Devices | FY 2023 / 2023-12-30 | ✓ | ✓ | ✓ | ✓ |
| Advanced Micro Devices | FY 2022 / 2022-12-31 | ✓ | ✓ | ✓ | ✓ |
| Advanced Micro Devices | FY 2021 / 2021-12-25 | ✓ | ✓ | ✓ | ✓ |
| JPMorgan Chase | FY 2025 / 2025-12-31 | ✓ | ✓ | ✓ | ✓ |
| JPMorgan Chase | FY 2024 / 2024-12-31 | ✓ | ✓ | ✓ | ✓ |
| JPMorgan Chase | FY 2023 / 2023-12-31 | ✓ | ✓ | ✓ | ✓ |
| JPMorgan Chase | FY 2022 / 2022-12-31 | ✓ | ✓ | ✓ | ✓ |
| JPMorgan Chase | FY 2021 / 2021-12-31 | ✓ | ✓ | ✓ | ✓ |
| Visa | FY 2025 / 2025-09-30 | ✓ | ✓ | ✓ | ✓ |
| Visa | FY 2024 / 2024-09-30 | ✓ | ✓ | ✓ | ✓ |
| Visa | FY 2023 / 2023-09-30 | ✓ | ✓ | ✓ | ✓ |
| Visa | FY 2022 / 2022-09-30 | ✓ | ✓ | ✓ | ✓ |
| Visa | FY 2021 / 2021-09-30 | ✓ | ✓ | ✓ | ✓ |
| Exxon Mobil | FY 2025 / 2025-12-31 | ✓ | ✓ | ✓ | ✓ |
| Exxon Mobil | FY 2024 / 2024-12-31 | ✓ | ✓ | ✓ | ✓ |
| Exxon Mobil | FY 2023 / 2023-12-31 | ✓ | ✓ | ✓ | ✓ |
| Exxon Mobil | FY 2022 / 2022-12-31 | ✓ | ✓ | ✓ | ✓ |
| Exxon Mobil | FY 2021 / 2021-12-31 | ✓ | ✓ | ✓ | ✓ |
| Johnson & Johnson | FY 2025 / 2025-12-28 | ✓ | ✓ | ✓ | ✓ |
| Johnson & Johnson | FY 2024 / 2024-12-29 | ✓ | ✓ | ✓ | ✓ |
| Johnson & Johnson | FY 2023 / 2023-12-31 | ✓ | ✓ | ✓ | ✓ |
| Johnson & Johnson | FY 2022 / 2023-01-01 | ✓ | ✓ | ✓ | ✓ |
| Johnson & Johnson | FY 2021 / 2022-01-02 | ✓ | ✓ | ✓ | ✓ |
| Mastercard | FY 2025 / 2025-12-31 | ✓ | ✓ | ✓ | ✓ |
| Mastercard | FY 2024 / 2024-12-31 | ✓ | ✓ | ✓ | ✓ |
| Mastercard | FY 2023 / 2023-12-31 | ✓ | ✓ | ✓ | ✓ |
| Mastercard | FY 2022 / 2022-12-31 | ✓ | ✓ | ✓ | ✓ |
| Mastercard | FY 2021 / 2021-12-31 | ✓ | ✓ | ✓ | ✓ |
| AbbVie | FY 2025 / 2025-12-31 | ✓ | ✓ | ✓ | ✓ |
| AbbVie | FY 2024 / 2024-12-31 | ✓ | ✓ | ✓ | ✓ |
| AbbVie | FY 2023 / 2023-12-31 | ✓ | ✓ | ✓ | ✓ |
| AbbVie | FY 2022 / 2022-12-31 | ✓ | ✓ | ✓ | ✓ |
| AbbVie | FY 2021 / 2021-12-31 | ✓ | ✓ | ✓ | ✓ |
| Palantir Technologies | FY 2025 / 2025-12-31 | ✓ | ✓ | ✓ | ✓ |
| Palantir Technologies | FY 2024 / 2024-12-31 | ✓ | ✓ | ✓ | ✓ |
| Palantir Technologies | FY 2023 / 2023-12-31 | ✓ | ✓ | ✓ | ✓ |
| Palantir Technologies | FY 2022 / 2022-12-31 | ✓ | ✓ | ✓ | ✓ |
| Palantir Technologies | FY 2021 / 2021-12-31 | ✓ | ✓ | ✓ | ✓ |

All 400 planned accessions have `✓`. This does **not** mean every mapped financial metric is present; the Gold issue mart and per-filing quality status answer that different question. No amendment was included. One JPMorgan accession, `0000019617-24-000326`, fell outside the SEC history file's own declared range. Two more, `0000019617-25-000270` and `0000019617-24-000611`, moved from history files `-007` and `-010` in the saved plan to `-008` and `-011` when ingested. Their form, filing date, report date, and primary document remained the same, and the later listing bytes are verified in the company run. The frozen source trace changed, so the runner keeps JPMorgan `PARTIAL` rather than hiding the discrepancy.

The latest company attempt for each issuer is below. `cohort-state.json` retains earlier attempts as well, including the Apple/Alphabet pilot and Palantir's first, parser-unready attempt. Run IDs name executions, not filings or Silver versions.

| Rank | Issuer | Latest run ID | Company result | Fiscal metadata |
| ---: | --- | --- | --- | --- |
| 1 | NVIDIA | `cohort_604d3d2c5f4149d3a77dde447b2717c1` | COMPLETE | COMPLETE |
| 2 | Apple | `cohort_02604c6e962f45a89a9aea061bddedc3` | COMPLETE | COMPLETE |
| 3 | Alphabet | `cohort_307b98a1f66e4e40b5a955d26bf19313` | COMPLETE | COMPLETE |
| 4 | Microsoft | `cohort_c5914f14082f46c78b153c43699b455d` | COMPLETE | COMPLETE |
| 5 | Amazon | `cohort_b5886d7965014f079f86c8276a32f83b` | COMPLETE | COMPLETE |
| 6 | Meta Platforms | `cohort_01b1e334619b4830bff5e15f28530fe2` | COMPLETE | COMPLETE |
| 7 | Broadcom | `cohort_60c1aebe04e448e880e7ff582140339f` | COMPLETE | COMPLETE |
| 8 | Tesla | `cohort_711543d6e0f4458fba6eb9abc79911f6` | COMPLETE | COMPLETE |
| 9 | Eli Lilly | `cohort_8a4fa32006bb4593a4bf176c47f5b7cc` | COMPLETE | COMPLETE |
| 10 | Berkshire Hathaway | `cohort_f47306f8ab2f4035be6e51a0a14f50b1` | COMPLETE | COMPLETE |
| 11 | Micron Technology | `cohort_dc77b81d864d4cecba25d717c189f09c` | COMPLETE | COMPLETE |
| 12 | Walmart | `cohort_529d16ac5e8647f59053cb9241a918be` | COMPLETE | COMPLETE |
| 13 | Advanced Micro Devices | `cohort_820c8a8d922a4897ae304aedae5d7a37` | COMPLETE | COMPLETE |
| 14 | JPMorgan Chase | `cohort_fbc83f78445046559c0776f03e9bdfb9` | PARTIAL | COMPLETE |
| 15 | Visa | `cohort_68ef206875454556a80e2b04149a2bca` | COMPLETE | COMPLETE |
| 16 | Exxon Mobil | `cohort_dd3430bb6bcd45bca8a4b98ec97378d1` | COMPLETE | COMPLETE |
| 17 | Johnson & Johnson | `cohort_59c580f4ea5c4929b266d6cc83396787` | COMPLETE | COMPLETE |
| 18 | Mastercard | `cohort_11789849c77546eab6eacdb137868110` | COMPLETE | COMPLETE |
| 19 | AbbVie | `cohort_c4d6eb336836432aa3081fdf21a379bb` | COMPLETE | COMPLETE |
| 20 | Palantir Technologies | `cohort_53440729375e4bc889cc4326bd7e6e68` | COMPLETE | COMPLETE |

## What landed in the shared dataset

The latest company attempts contain 341 newly completed and 59 safely reused filings, with no failed or unattempted accession. All 400 planned accessions occur exactly once in the active Silver catalog, filing metadata, fiscal metadata version 3, and `gold.dim_filings`. The 400 canonical Bronze publications and active Silver pointers have complete stage status in their saved company records. The catalog exposes 601,103 accepted facts, 643,624 dimension rows, and no rejected facts. Gold has **4,015 financial rows** (3,349 reported and 666 derived) and **487 issue rows**. The issue count is a count of issue occurrences, not a count of failed filings.

| Issuer | Silver facts | Dimension rows | Rejected | Gold metrics | Gold issues |
| --- | ---: | ---: | ---: | ---: | ---: |
| NVIDIA | 19,050 | 10,159 | 0 | 195 | 20 |
| Apple | 15,505 | 11,806 | 0 | 230 | 0 |
| Alphabet | 26,821 | 22,822 | 0 | 230 | 0 |
| Microsoft | 26,919 | 23,664 | 0 | 230 | 0 |
| Amazon | 20,448 | 10,968 | 0 | 175 | 70 |
| Meta Platforms | 17,642 | 10,662 | 0 | 230 | 0 |
| Broadcom | 22,818 | 14,429 | 0 | 196 | 20 |
| Tesla | 23,401 | 15,539 | 0 | 230 | 0 |
| Eli Lilly | 28,512 | 38,490 | 0 | 175 | 40 |
| Berkshire Hathaway | 39,197 | 56,555 | 0 | 139 | 76 |
| Micron Technology | 22,091 | 19,318 | 0 | 230 | 0 |
| Walmart | 17,092 | 10,702 | 0 | 170 | 55 |
| Advanced Micro Devices | 17,498 | 13,717 | 0 | 210 | 20 |
| JPMorgan Chase | 140,664 | 195,620 | 0 | 145 | 60 |
| Visa | 24,046 | 19,816 | 0 | 165 | 40 |
| Exxon Mobil | 24,879 | 24,848 | 0 | 214 | 15 |
| Johnson & Johnson | 43,849 | 77,137 | 0 | 230 | 3 |
| Mastercard | 23,687 | 17,701 | 0 | 182 | 32 |
| AbbVie | 31,285 | 38,868 | 0 | 210 | 35 |
| Palantir Technologies | 15,699 | 10,803 | 0 | 229 | 1 |

Each issuer has 20 active filings, 20 filing metadata rows, 20 complete fiscal metadata version-3 rows, and 20 Gold filing summaries. The per-company audit and complete ordered Gold samples are in `cohort-audit.json`. Its check joined every selected Gold occurrence ID to the accepted Silver fact and compared CIK, accession, source document, source checksum, and exact Decimal value. It checked 8,406 metric-support occurrences and 385 issue-support occurrences. For every derived row, the predecessor came from the same company and the output equaled the exact current cumulative value minus the exact predecessor. The 898 files captured before the later company runs, including the Apple/Alphabet pilot publications and evidence, still match their saved SHA-256 values. Apple and Alphabet's complete ordered records in all three Gold marts also match the corrected pilot snapshots, not just their row counts.

A few rows show what the lineage check means in practice. These are stored Decimal values, with the fixed scale kept here so the sample is directly comparable to the full record. USD and USD/share stand for the original XBRL unit expressions in Gold. Full source-document names, SHA-256 values, and supporting occurrence arrays remain in the mart; the ignored pilot snapshots hold the complete Apple and Alphabet records.

| Issuer and fiscal focus | Accession / metric | Period and value | Source lineage |
| --- | --- | --- | --- |
| Apple FY 2021 | `0000320193-21-000105` / revenue | Reported annual, 2020-09-27–2021-09-25: `365817000000.000000000000000000` USD | `8a4e7deb719f9c4ddc7a156ee5af9e7ce4e1f447fdae235414d71f6d0407795c` |
| AMD FY 2021 | `0000002488-22-000016` / diluted EPS | Reported annual: `2.570000000000000000` USD/share | `0630b99139091e3ebbc60d81cb8da753fb2902c274fb76d6e3fffb1f28766cee` |
| AMD FY 2021 Q1 | `0000002488-21-000056` / net income | Reported quarter, 2020-12-27–2021-03-27: `555000000.000000000000000000` USD | `77bf39b15690ca08e407625be944aba2dd5f661440ee044b541c4aaf05d9d129` |
| Alphabet FY 2021 Q2 | `0001652044-21-000047` / operating cash flow | Reported year-to-date, 2021-01-01–2021-06-30: `41179000000.000000000000000000` USD | `f72c8a4149be9f4cee093de6b098c45d0403c7f0cae989bfb28d5ea7a8bb9396` |
| Alphabet FY 2021 Q1 | `0001652044-21-000020` / total assets | Reported instant, 2021-03-31: `327095000000.000000000000000000` USD | `2135968bc8ac7f5a66563f732dcf73b08f5097ac4d4b97bb3df0105f16092910` |
| Apple FY 2021 Q2 | `0000320193-21-000056` / operating cash flow | Derived quarter: `62744000000.000000000000000000` − `38763000000.000000000000000000` = `23981000000.000000000000000000` USD | Current `91d05534fa4a1114ae7c4d114ff74825ff04540e3c1e19cf86f0b888da28aa29`; predecessor `0000320193-21-000010` / `402492f1eae66ade8f6436cead7120ba7146856ce51aa4fdd38bb275616c2a5d` |

`gold.dim_filings` reports 190 `COMPLETE` and 210 `PARTIAL` financial-quality summaries. The issue mart has 364 `ERROR` and 123 `WARNING` rows. None indicates a failed dbt node. There are 325 `NO_ELIGIBLE_METRIC` and 15 `NO_ELIGIBLE_REVENUE` occurrences where the current mapped candidates do not satisfy the selection rules; 85 revenue and five other metric value conflicts where eligible facts disagree; one unresolved accuracy comparison; 48 durations that do not fit the supported period classes; and eight missing predecessor cumulative values that prevent a derived quarter. Some issue codes have both error and warning severity according to the underlying metric. JPMorgan accounts for 60 issue rows, including 15 absent eligible revenue occurrences; its source-listing provenance warning is a separate company-stage finding. Check the accession's issue rows before using its metric for analysis.

The final dbt attempt is `dbt/artifacts-77e2d8fb400a482f9a7ca1cadb4e754b/`, invocation `9278f159-292d-4596-8a6a-c369d13effe7`. Its `target/run_results.json` has 514 passing tests and 24 successful model/seed nodes, with no failures or skips, in 29m04.79s. That invocation and the artifact path match the saved cohort state, and all 20 saved company-record checksums match their files. The read-only `dashboard_inspect` command returned 20 CIKs, 400 filings, 4,015 metrics, and 487 issues; its terminal output is `dashboard-inspect-after-resource-fix.log`. The original `data/query/sec_edgar.duckdb` checksum remained `095684c1c2572d6e202b44b262324854b813f07d61e8e9a5751b4d1c0284b52b` before and after the cohort work. The isolated cohort database stayed at `12c5dac06521aa8deb0b14ad73315ef79d5e784c51766e0355abc544a507ff99` across the final reader and UI checks.

## Architecture exercised

Historical discovery follows only CIK-specific files referenced by a verified main submissions response. It validates their parallel arrays, source identity, paths, and overlaps before selecting a row. The original bytes, retrieval URL/time, size, and SHA-256 are saved in company run evidence. Filing metadata rechecks the particular listing used for its accession before insertion. This is why older filings get the same verifiable provenance as recent filings.

The cohort command is a sequential local coordinator around the existing company APIs. It freezes a plan, processes each issuer into shared Bronze/Silver roots, refreshes the catalog for metadata, then asks dbt to build Gold once for the batch. Every company attempt has a new run ID and a saved outcome for each accession. Resume checks the stored plan and completed run record before skipping work; Bronze, Silver, and metadata still apply their own reuse rules. The local lock prevents two cohort commands from writing together. Other database readers need to be closed during writes.

The Apple/Alphabet pilot exposed a fiscal-parser limit: the SEC's official `dei/2021q4` namespace was not recognized, leaving two Alphabet filings partial. Extraction version 3 adds that quarter-versioned DEI namespace and `dei/2022q4`, as documented by the [SEC DEI taxonomy index](https://xbrl.sec.gov/dei/). Version 2 rows remain as evidence; version 3 loads the corrected fiscal fields and Gold chooses the highest supported version for the same active document. The corrected pilot had 20 complete fiscal rows and 230 financial metrics for each issuer, with no Gold quality issues. All 538 dbt nodes passed. A later pilot rerun reused all 40 Bronze/Silver filings, returned `ALREADY_EXISTS` for 40 fiscal rows, and left the complete ordered Gold records unchanged.

The first full pass found a different older-filing format at Palantir accession `0001193125-21-159222` (FY 2021 Q1). Its Data Files table labels the verified XML `XBRL INSTANCE DOCUMENT`, whereas the original readiness rule accepted only `EXTRACTED XBRL INSTANCE DOCUMENT`. Bronze had correctly saved all 14 source/evidence files but marked the filing `parser_ready=false`, so Silver would not read it. The rule now accepts either **exact** instance label and still rejects zero or multiple candidates. A copy of the saved XML produced 481 accepted facts and no rejected facts in a read-only extraction probe. The original 14-file Bronze publication and its checksums remain under `archived-bronze-policy/` in the ignored dataset; a normal rerun then published a new verified canonical Bronze filing, reused Palantir's other 19 filings, and loaded the missing fiscal metadata. No source bytes were edited to make the filing pass.

The first Streamlit smoke run displayed the cohort totals and Apple's filings, then exhausted DuckDB's default memory budget while rereading the Gold views after a filing selection. The reader now opens its read-only connection with one worker and a 4GB limit, matching the final batch build's local budget while preserving insertion order. The same three Gold views, transaction, schema checks, records, and UI selections are unchanged. With that setting, the read-only inspector completed against the full cohort and the browser rendered Apple's FY 2025 10-K detail: `COMPLETE`, 12 financial rows, no errors or warnings, no missing critical metrics, and distinct reported annual (`416,161,000,000` USD) and derived Q4 (`102,466,000,000` USD) revenue rows. The browser and local server were closed after this read-only check; `dashboard-smoke.txt` records the result.

## Validation and remaining review

The final code passed 1,283 Python tests, Ruff lint and format checks, mypy across `src` and `tests`, and an offline package build whose wheel includes the cohort runner and dashboard reader setting. The final synthetic dbt regression, run without an SEC contact variable in a fresh temporary work directory, passed all 538 nodes and its fixed Decimal, ordering, warning, and lineage checks: 33 financial rows, three filings, three expected warnings. Its saved console output is `offline-ci-final.log` under this cohort's ignored data root. Synthetic fixtures are a code regression check; the real 400-filing counts above come from the separate live cohort and its own dbt artifacts. `git diff --check` passed.

The real-data audit is `cohort-audit.json` and the console log is `cohort-audit.log`. `quality-audit.log` records the Gold quality-status, severity, and issue-code counts. `dashboard-inspect-after-resource-fix.log` and `dashboard-smoke.txt` record the read-only demo. `full-cohort-run.log` and `palantir-recovery-and-build.log` show the company attempts and final batch build. The saved plan, original SEC listing bytes, company run records, Bronze manifests, Silver versions/pointers, and dbt artifacts remain under the ignored data root.

JPMorgan's three affected accession rows need a comparison between the frozen `cohort-plan.json` and the later saved company listings. The history-file range mismatch and the two moves between numbered files are documented above. The identity fields match and the filings are present, but the source-listing trace changed. Gold quality needs separate review for each intended metric; missing, conflicting, and unsupported values remain in the issue mart. A plain resume validates the saved checkpoint and leaves the source-provenance warning in place unless the underlying evidence changes:

```sh
uv run --no-sync python -m sec_edgar_lakehouse.pipeline.cohort_run resume \
  --manifest config/cohort-sp500-top20-2026-10-15.json \
  --data-root data/cohort-v1-dc5b138b-e4da-4419-97d2-f1546ff6bf1d \
  --dbt-timeout-seconds 43200
```

## Reproduce it

Keep the dashboard and other readers off the destination database while a run writes. These commands assume the locked dbt and dashboard groups are installed and that `SEC_USER_AGENT` is already set in the shell running the command. A new attempt needs a **fresh ignored data root**; resume uses the same root and manifest.

```sh
uv sync --locked --group dbt --group dashboard
: "${SEC_USER_AGENT:?Set an identified SEC contact in this shell}"
cohort_root=$(mktemp -d data/cohort-v1.XXXXXX)
manifest=config/cohort-sp500-top20-2026-10-15.json
uv run --no-sync python -m sec_edgar_lakehouse.pipeline.cohort_run plan \
  --manifest "$manifest" --data-root "$cohort_root"
uv run --no-sync python -m sec_edgar_lakehouse.pipeline.cohort_run execute \
  --manifest "$manifest" --data-root "$cohort_root" --dbt-timeout-seconds 43200
# Use this if a company attempt or the build needs another try:
uv run --no-sync python -m sec_edgar_lakehouse.pipeline.cohort_run resume \
  --manifest "$manifest" --data-root "$cohort_root" --dbt-timeout-seconds 43200
# If company work is already complete and only Gold needs rebuilding:
uv run --no-sync python -m sec_edgar_lakehouse.pipeline.cohort_run resume \
  --manifest "$manifest" --data-root "$cohort_root" \
  --build-after-batch --dbt-timeout-seconds 43200
```

The runner keeps its plan and state as `cohort-plan.json` and `cohort-state.json`. Each company has a saved `runs/cik=<CIK>/run_id=<run-id>/run.json`, source listings, and accession outcomes; each dbt attempt has a separate `dbt/artifacts-<id>/` directory. The state names the actual artifact and profile paths. It uses one dbt job with one DuckDB engine thread, a 4GB limit, and insertion-order preservation. An interrupted or failed Gold build must be checked from its artifacts before treating the marts as current.

For local inspection, the ignored `data/dashboard-current.duckdb` link points to this cohort's database. Running `uv run streamlit run dashboard_app.py` without a path override therefore opens this dataset in the current checkout. The original `data/query/sec_edgar.duckdb` remains in place.

The offline regression and the read-only demo are separate from live SEC coverage:

```sh
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy src tests
env -u SEC_USER_AGENT uv run --no-sync pytest -q
uv build --offline
ci_work=$(mktemp -d "${TMPDIR:-/tmp}/sec-edgar-cohort-ci.XXXXXX")
env -u SEC_USER_AGENT uv run --no-sync python scripts/ci/validate_offline_dbt.py \
  --work-dir "$ci_work"
uv run --no-sync python -m sec_edgar_lakehouse.dashboard.dashboard_inspect \
  --database "$cohort_root/query/sec_edgar.duckdb"
SEC_EDGAR_DUCKDB_PATH="$cohort_root/query/sec_edgar.duckdb" \
  uv run --no-sync streamlit run dashboard_app.py
git diff --check
```
