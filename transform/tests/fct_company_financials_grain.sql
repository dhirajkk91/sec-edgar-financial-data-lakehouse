-- One row per company, fiscal year and fiscal period.
select cik, fiscal_year, fiscal_period, count(*) as row_count
from {{ ref('fct_company_financials') }}
group by cik, fiscal_year, fiscal_period
having count(*) <> 1
