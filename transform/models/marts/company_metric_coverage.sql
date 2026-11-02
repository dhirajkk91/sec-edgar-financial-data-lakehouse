-- One row per company and metric: how many of the company's fiscal periods have a value.
-- Every FY and quarter row in fct_company_financials is expected to have each of the eight
-- metrics, except Q4 diluted EPS, which Gold never derives. A missing value is a gap that
-- metric_quality_issues explains; this model only counts.
with metric_values as (
    select cik, fiscal_year, fiscal_period, metric_code, value_decimal
    from {{ ref('fct_company_financials') }}
    unpivot include nulls (value_decimal for metric_code in (
        revenue, net_income, diluted_eps, operating_cash_flow, capital_expenditures,
        total_assets, total_liabilities, cash_and_equivalents
    ))
)
select
    cik,
    metric_code,
    count(*)::integer as periods_expected,
    count(value_decimal)::integer as periods_present,
    (count(value_decimal) / count(*))::double as coverage
from metric_values
where not (metric_code = 'diluted_eps' and fiscal_period = 'Q4')
group by cik, metric_code
