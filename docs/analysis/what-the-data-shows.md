# What the data shows

The pipeline exists to produce numbers I can trust, so this is what I found when I used them. This is a dated analysis of the frozen 20-company cohort (filings through October 15, 2026); the live dataset now covers the S&P 500 and refreshes on weeknights, and the [Athena queries](../../infra/aws/queries/) ask similar questions of it. It asks four questions of the 20 companies in the cohort: who grew fastest, who is most profitable, whose profits turn into cash, and how much the cloud companies are spending. Each section has the SQL, the result, what I read into it, and what could make that reading wrong.

The questions came out of an [exploratory notebook](../../notebooks/exploratory-analysis.ipynb) that looks at the data first: missing values, distributions, outliers, trends and seasonality. The same findings are in the dashboard's **Insights** view ([live](https://sec-lakehouse.streamlit.app/)).

## The data and its limits

- **Companies.** The 20 largest S&P 500 issuers by market cap on October 15, 2026 ([cohort file](../../config/cohort-sp500-top20-2026-10-15.json)). They were chosen by size, so the results describe the largest companies, not typical ones.
- **Years.** Five fiscal years per company, from its own 10-Ks. Fiscal years end in different months: NVIDIA's FY2026 ended in January 2026, Microsoft's in June 2026 and Micron's in early September 2026. "Latest year" means each company's most recent fiscal year with revenue.
- **Values.** Everything comes from [`gold.company_insights`](../../transform/models/marts/company_insights.sql) and `gold.fct_company_financials`, which are built only from Gold's selected values. A value Gold withheld is missing here, never treated as zero.
- **Coverage.** Gold has 92.6% of the company-metric values it could have, and the gaps sit in the balance sheet: five companies never report a consolidated total-liabilities figure. Revenue, net income and operating cash flow are nearly complete, so this analysis uses those. The notebook's missing-values section has the details.
- **Definitions.** Free cash flow is operating cash flow minus capital expenditures. Cash conversion is free cash flow divided by net income, and only exists when net income is positive. Revenue growth is the compound annual rate between the first and latest fiscal years.

Every query below runs against the committed demo database, for example with `duckdb -readonly demo/sec_edgar_demo.duckdb`.

## 1. Who grew fastest?

```sql
select coalesce(c.primary_ticker, c.company_name) as company,
       'FY' || i.first_fiscal_year || '–FY' || i.latest_fiscal_year as years,
       round(i.first_revenue / 1e9, 1) as first_revenue_bn,
       round(i.latest_revenue / 1e9, 1) as latest_revenue_bn,
       round(i.revenue_cagr * 100, 1) as growth_per_year_pct
from gold.company_insights as i
join gold.dim_companies as c using (cik)
where i.revenue_cagr is not null
order by i.revenue_cagr desc;
```

| Company | Years | First revenue ($B) | Latest revenue ($B) | Growth per year |
| --- | --- | ---: | ---: | ---: |
| NVDA | FY2022–FY2026 | 26.9 | 215.9 | 68.3% |
| MU | FY2022–FY2026 | 30.8 | 133.2 | 44.3% |
| PLTR | FY2021–FY2025 | 1.5 | 4.5 | 30.5% |
| AVGO | FY2021–FY2025 | 27.5 | 63.9 | 23.5% |
| LLY | FY2021–FY2025 | 28.3 | 65.2 | 23.2% |
| AMD | FY2021–FY2025 | 16.4 | 34.6 | 20.5% |
| TSLA | FY2021–FY2025 | 53.8 | 94.8 | 15.2% |
| MA | FY2021–FY2025 | 18.9 | 32.8 | 14.8% |
| META | FY2021–FY2025 | 117.9 | 201.0 | 14.3% |
| MSFT | FY2022–FY2026 | 198.3 | 331.8 | 13.7% |
| V | FY2021–FY2025 | 24.1 | 40.0 | 13.5% |
| GOOGL | FY2021–FY2025 | 257.6 | 402.8 | 11.8% |
| AMZN | FY2021–FY2025 | 469.8 | 716.9 | 11.1% |
| JPM | FY2021–FY2025 | 121.6 | 182.4 | 10.7% |
| BRK-B | FY2021–FY2025 | 276.1 | 371.4 | 7.7% |
| WMT | FY2022–FY2026 | 572.8 | 713.2 | 5.6% |
| Exxon Mobil | FY2021–FY2025 | 285.6 | 332.2 | 3.9% |
| AAPL | FY2021–FY2025 | 365.8 | 416.2 | 3.3% |
| ABBV | FY2021–FY2025 | 56.2 | 61.2 | 2.1% |
| JNJ | FY2021–FY2025 | 93.8 | 94.2 | 0.1% |

The median is 13.6% a year.

**What it means.** The fastest growers are chip companies: NVIDIA grew eightfold in four years, and Micron, Broadcom and AMD are all in the top six. Eli Lilly is the exception outside tech. At the bottom are the largest, most mature businesses: Apple, Walmart and Exxon grew by single digits a year.

**Caveats.**

- **Micron's rate is mostly one year.** Its revenue was $37.4B in FY2025 and $133.2B in FY2026, a 3.6× jump. Revenue fell to $15.5B in FY2023 before that. A compound rate between two endpoints hides a cycle like this; the Companies view shows the full path.
- **Johnson & Johnson's 0.1% is not like for like.** Its FY2021 revenue of $93.8B, as reported in the FY2021 10-K, includes the consumer health business it separated as Kenvue in 2023. From FY2023, the first year without it ($85.2B), to FY2025 ($94.2B), it grew 5.2% a year. Gold keeps each filing's own figures and doesn't swap in later restatements, so any comparison across a spin-off needs this check.
- Windows don't line up: four of the 20 companies' latest year is FY2026, the rest FY2025.

## 2. Who is most profitable?

```sql
select coalesce(c.primary_ticker, c.company_name) as company,
       i.latest_fiscal_year as fiscal_year,
       round(i.latest_net_margin * 100, 1) as net_margin_pct,
       round(i.latest_free_cash_flow_margin * 100, 1) as fcf_margin_pct
from gold.company_insights as i
join gold.dim_companies as c using (cik)
order by i.latest_net_margin desc nulls last;
```

| Company | Fiscal year | Net margin | Free cash flow margin |
| --- | --- | ---: | ---: |
| MU | 2026 | 63.8% | 44.3% |
| NVDA | 2026 | 55.6% | 44.8% |
| V | 2025 | 50.1% | 53.9% |
| MA | 2025 | 45.6% | 52.3% |
| MSFT | 2026 | 40.3% | 20.2% |
| PLTR | 2025 | 36.3% | 46.9% |
| AVGO | 2025 | 36.2% | 42.1% |
| GOOGL | 2025 | 32.8% | 18.2% |
| LLY | 2025 | 31.7% | 13.8% |
| JPM | 2025 | 31.3% | n/a |
| META | 2025 | 30.1% | 22.9% |
| JNJ | 2025 | 28.5% | 20.9% |
| AAPL | 2025 | 26.9% | 23.7% |
| BRK-B | 2025 | 18.0% | 6.7% |
| AMD | 2025 | 12.5% | 19.4% |
| AMZN | 2025 | 10.8% | 1.1% |
| Exxon Mobil | 2025 | 8.7% | 7.1% |
| ABBV | 2025 | 6.9% | 29.1% |
| TSLA | 2025 | 4.0% | 6.6% |
| WMT | 2026 | 3.1% | 2.1% |

**What it means.** Two kinds of business lead on both measures: chip makers riding AI demand (Micron, NVIDIA) and the payment networks (Visa, Mastercard). Visa and Mastercard keep more as free cash than they report as profit, which fits companies that need little equipment to grow. At the other end, Walmart keeps about 3 cents of each sales dollar, which is normal for a large retailer; it is a high-volume business, not a weak one.

**Caveats.**

- **One year is a snapshot.** Micron's net margin was 22.8% a year earlier, in FY2025, and negative in FY2023. Its 63.8% is the top of a memory price cycle, not a steady state.
- **AbbVie's 6.9% understates the business.** See section 3: a single non-cash charge was larger than its reported profit.
- JPMorgan has no free cash flow margin because it has no capital expenditures figure in Gold (the notebook's missing-values section explains why). For a bank, free cash flow isn't a useful measure anyway.

## 3. Whose profits turn into cash?

```sql
select coalesce(c.primary_ticker, c.company_name) as company,
       round(i.latest_net_income / 1e9, 1) as net_income_bn,
       round(i.latest_free_cash_flow / 1e9, 1) as free_cash_flow_bn,
       round(i.cash_conversion * 100) as conversion_pct
from gold.company_insights as i
join gold.dim_companies as c using (cik)
order by i.cash_conversion desc nulls last;
```

| Company | Net income ($B) | Free cash flow ($B) | Conversion |
| --- | ---: | ---: | ---: |
| ABBV | 4.2 | 17.8 | 422% |
| TSLA | 3.8 | 6.2 | 164% |
| AMD | 4.3 | 6.7 | 155% |
| PLTR | 1.6 | 2.1 | 129% |
| AVGO | 23.1 | 26.9 | 116% |
| MA | 15.0 | 17.2 | 115% |
| V | 20.1 | 21.6 | 108% |
| AAPL | 112.0 | 98.8 | 88% |
| Exxon Mobil | 28.8 | 23.6 | 82% |
| NVDA | 120.1 | 96.7 | 81% |
| META | 60.5 | 46.1 | 76% |
| JNJ | 26.8 | 19.7 | 73% |
| MU | 85.0 | 59.0 | 69% |
| WMT | 21.9 | 14.9 | 68% |
| GOOGL | 132.2 | 73.3 | 55% |
| MSFT | 133.7 | 67.0 | 50% |
| LLY | 20.6 | 9.0 | 43% |
| BRK-B | 67.0 | 25.0 | 37% |
| AMZN | 77.7 | 7.7 | 10% |
| JPM | 57.0 | n/a | n/a |

Seven of the 19 companies with a figure turned at least 100% of net income into free cash flow.

**What it means.** The four cloud companies, Alphabet, Microsoft, Amazon and Meta, all sit in the bottom half, and the reason is capital spending (section 4). Eli Lilly's 43% has the same cause on a smaller scale: its capex rose from $1.3B to $7.8B as it built manufacturing capacity.

**The outliers have specific causes.**

- **AbbVie at 422%.** Its FY2025 10-K reports net income of $4.2B, operating cash flow of $19.0B, and a $6.5B charge for the change in fair value of contingent consideration, a liability for future payments on acquisitions. The charge reduces profit but isn't paid in cash that year. I found it in the filing's XBRL facts in Silver; Gold doesn't track this item.
- **Berkshire at 37%.** Berkshire's net income includes gains and losses on its stock holdings, which aren't cash, so conversion says little about its operating businesses.

**Caveat.** JPMorgan is left out: no capex figure means no free cash flow.

## 4. How much are the cloud companies spending?

```sql
select coalesce(c.primary_ticker, c.company_name) as company, f.fiscal_year,
       round(f.capital_expenditures / 1e9, 1) as capex_bn,
       round(f.operating_cash_flow / 1e9, 1) as operating_cash_flow_bn
from gold.fct_company_financials as f
join gold.dim_companies as c using (cik)
where f.fiscal_period = 'FY'
  and c.primary_ticker in ('AMZN', 'MSFT', 'GOOGL', 'META')
order by company, f.fiscal_year;
```

| Company | First year capex ($B) | Latest year capex ($B) |
| --- | ---: | ---: |
| AMZN | 61.1 (FY2021) | 131.8 (FY2025) |
| MSFT | 23.9 (FY2022) | 115.9 (FY2026) |
| GOOGL | 24.6 (FY2021) | 91.4 (FY2025) |
| META | 18.6 (FY2021) | 69.7 (FY2025) |
| **Total** | **128.2** | **408.8** |

**What it means.** Together they spent 3.2 times as much in their latest year as in their first, and almost all of the increase came from FY2024 on: all four were between $19B and $64B a year through FY2023. Amazon's FY2025 operating cash flow was $139.5B and capex took $131.8B of it, 94%, which is why only 10% of its profit became free cash. This is the link between section 3 and section 4: these companies are very profitable, and they are putting most of that cash back into buildings and equipment.

**Caveats.**

- Gold has total capital expenditures, not what they were for. Data centers and AI chips are the likely bulk of the increase, but this data can't separate them from other property and equipment.
- Microsoft's latest year is FY2026, which ended in June 2026; the others' is FY2025. The totals add each company's own latest year, not one calendar year.

## What I'd look at next

- Compare across the J&J spin-off using the comparative figures in a later 10-K, and flag spin-offs in the data instead of in a note.
- Show Micron's cycle quarter by quarter, where the swing is clearer than in an annual rate.
- Decide whether to derive total liabilities, which five companies never report, as total liabilities and equity minus equity. It is a new derivation that would need its own rule and tests for noncontrolling interests and temporary equity.
