-- Latest fiscal year's revenue growth, ranked within each SEC sector.
-- Window functions: RANK and a sector median for comparison.
WITH latest AS (
    SELECT f.cik, f.fiscal_year, f.revenue, f.revenue_yoy_growth,
           ROW_NUMBER() OVER (PARTITION BY f.cik ORDER BY f.fiscal_year DESC) AS recency
    FROM sec_gold.fct_company_financials AS f
    WHERE f.fiscal_period = 'FY' AND f.revenue_yoy_growth IS NOT NULL
)
SELECT COALESCE(c.sector, 'Unknown') AS sector,
       COALESCE(c.primary_ticker, c.company_name) AS company,
       l.fiscal_year,
       ROUND(CAST(l.revenue AS DOUBLE) / 1e9, 1) AS revenue_bn,
       ROUND(l.revenue_yoy_growth * 100, 1) AS growth_pct,
       RANK() OVER (PARTITION BY c.sector ORDER BY l.revenue_yoy_growth DESC) AS rank_in_sector,
       ROUND(APPROX_PERCENTILE(l.revenue_yoy_growth, 0.5) OVER (PARTITION BY c.sector) * 100, 1)
           AS sector_median_pct
FROM latest AS l
JOIN sec_gold.dim_companies AS c USING (cik)
WHERE l.recency = 1
ORDER BY sector, rank_in_sector;
