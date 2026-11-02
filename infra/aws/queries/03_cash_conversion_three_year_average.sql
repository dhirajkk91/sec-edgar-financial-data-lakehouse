-- Free cash flow as a share of net income per year, with a rolling 3-year average
-- so a single non-cash charge doesn't decide the picture. Loss years are left out.
SELECT COALESCE(c.primary_ticker, c.company_name) AS company,
       f.fiscal_year,
       ROUND(CAST(f.free_cash_flow AS DOUBLE) / CAST(f.net_income AS DOUBLE) * 100) AS conversion_pct,
       ROUND(
           AVG(CAST(f.free_cash_flow AS DOUBLE) / CAST(f.net_income AS DOUBLE)) OVER (
               PARTITION BY f.cik ORDER BY f.fiscal_year
               ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
           ) * 100
       ) AS three_year_avg_pct
FROM sec_gold.fct_company_financials AS f
JOIN sec_gold.dim_companies AS c USING (cik)
WHERE f.fiscal_period = 'FY' AND f.net_income > 0 AND f.free_cash_flow IS NOT NULL
ORDER BY company, f.fiscal_year;
