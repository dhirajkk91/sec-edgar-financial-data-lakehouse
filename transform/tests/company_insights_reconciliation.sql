-- Each insight must agree with the annual row it came from, and the growth rate must
-- compound the first year's revenue back into the latest year's revenue.
with annual as (
    select * from {{ ref('fct_company_financials') }} where fiscal_period = 'FY'
)
select 'latest_year' as problem, i.cik
from {{ ref('company_insights') }} as i
join annual as a on i.cik = a.cik and i.latest_fiscal_year = a.fiscal_year
where i.latest_revenue is distinct from a.revenue
    or i.latest_net_income is distinct from a.net_income
    or i.latest_free_cash_flow is distinct from a.free_cash_flow
    or i.latest_net_margin is distinct from a.net_margin
    or i.latest_free_cash_flow_margin is distinct from a.free_cash_flow_margin
union all
select 'first_year', i.cik
from {{ ref('company_insights') }} as i
join annual as a on i.cik = a.cik and i.first_fiscal_year = a.fiscal_year
where i.first_revenue is distinct from a.revenue
union all
select 'growth_does_not_compound', cik
from {{ ref('company_insights') }}
where revenue_cagr is not null
    and abs(
        first_revenue::double
        * power(1 + revenue_cagr, latest_fiscal_year - first_fiscal_year)
        / latest_revenue::double - 1
    ) > 1e-9
union all
select 'growth_without_two_years', cik
from {{ ref('company_insights') }}
where revenue_cagr is not null and latest_fiscal_year <= first_fiscal_year
union all
select 'conversion_without_positive_income', cik
from {{ ref('company_insights') }}
where cash_conversion is not null and not latest_net_income > 0
