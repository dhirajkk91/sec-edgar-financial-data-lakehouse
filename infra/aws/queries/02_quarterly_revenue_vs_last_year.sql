-- Each fiscal quarter against the same quarter a year earlier.
-- LAG over the same fiscal_period compares like with like across fiscal calendars.
SELECT COALESCE(c.primary_ticker, c.company_name) AS company,
       f.fiscal_year,
       f.fiscal_period,
       ROUND(CAST(f.revenue AS DOUBLE) / 1e9, 2) AS revenue_bn,
       ROUND(
           (CAST(f.revenue AS DOUBLE)
            / NULLIF(CAST(LAG(f.revenue) OVER w AS DOUBLE), 0) - 1) * 100, 1
       ) AS vs_same_quarter_last_year_pct
FROM sec_gold.fct_company_financials AS f
JOIN sec_gold.dim_companies AS c USING (cik)
WHERE f.fiscal_period IN ('Q1', 'Q2', 'Q3', 'Q4') AND f.revenue IS NOT NULL
WINDOW w AS (PARTITION BY f.cik, f.fiscal_period ORDER BY f.fiscal_year)
ORDER BY company, f.fiscal_year, f.fiscal_period;
