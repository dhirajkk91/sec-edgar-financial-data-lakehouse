-- Every pivoted value must equal the Gold metric row it came from, and FCF must equal
-- operating cash flow minus capital expenditures whenever both exist.
with pivoted as (
    select cik, accession_number, fiscal_period, metric_code, value_decimal
    from {{ ref('fct_company_financials') }}
    unpivot (value_decimal for metric_code in (
        revenue, net_income, diluted_eps, operating_cash_flow, capital_expenditures
    ))
), source as (
    select cik, accession_number, fiscal_period_focus as fiscal_period, metric_code, value_decimal
    from {{ ref('fct_financial_metrics') }}
    where (fiscal_period_focus = 'FY' and period_label = 'annual')
        or (fiscal_period_focus in ('Q1', 'Q2', 'Q3', 'Q4')
            and period_label in ('quarter', 'derived_quarter'))
)
select 'value_mismatch' as problem, p.cik, p.accession_number, p.fiscal_period, p.metric_code
from pivoted as p
left join source as s
    on p.cik = s.cik and p.accession_number = s.accession_number
    and p.fiscal_period = s.fiscal_period and p.metric_code = s.metric_code
where s.value_decimal is distinct from p.value_decimal
union all
select 'free_cash_flow', cik, accession_number, fiscal_period, 'free_cash_flow'
from {{ ref('fct_company_financials') }}
where free_cash_flow is distinct from operating_cash_flow - capital_expenditures
