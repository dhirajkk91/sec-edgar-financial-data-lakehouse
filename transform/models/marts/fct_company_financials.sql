-- One row per company and fiscal period (FY, Q1-Q4) with each metric side by side.
-- Values come only from fct_financial_metrics: this model pivots them and adds simple ratios.
-- It never selects, derives or fills a financial value. A missing metric stays NULL.
with durations as (
    -- The standalone value for the period: the full year for FY, three months for a quarter.
    -- Year-to-date rows are left out; derived quarters are used only where Gold produced them.
    select *
    from {{ ref('fct_financial_metrics') }}
    where (fiscal_period_focus = 'FY' and period_label = 'annual')
        or (fiscal_period_focus in ('Q1', 'Q2', 'Q3', 'Q4')
            and period_label in ('quarter', 'derived_quarter'))
), periods as (
    select
        cik,
        fiscal_year_focus as fiscal_year,
        fiscal_period_focus as fiscal_period,
        accession_number,
        any_value(form) as form,
        any_value(filing_date) as filing_date,
        any_value(report_date) as report_date,
        min(period_start) as period_start,
        max(period_end) as period_end,
        max(value_decimal) filter (where metric_code = 'revenue') as revenue,
        max(value_decimal) filter (where metric_code = 'net_income') as net_income,
        max(value_decimal) filter (where metric_code = 'diluted_eps') as diluted_eps,
        max(value_decimal) filter (where metric_code = 'operating_cash_flow')
            as operating_cash_flow,
        max(value_decimal) filter (where metric_code = 'capital_expenditures')
            as capital_expenditures,
        coalesce(
            list(metric_code order by metric_code) filter (where value_origin = 'derived'),
            []::varchar[]
        ) as derived_metric_codes
    from durations
    group by cik, fiscal_year_focus, fiscal_period_focus, accession_number
), balances as (
    -- Balance-sheet values at the end of the filing's reporting period.
    -- A 10-K's year-end balance belongs to both its FY row and its Q4 row.
    select
        cik,
        accession_number,
        max(value_decimal) filter (where metric_code = 'total_assets') as total_assets,
        max(value_decimal) filter (where metric_code = 'total_liabilities')
            as total_liabilities,
        max(value_decimal) filter (where metric_code = 'cash_and_equivalents')
            as cash_and_equivalents
    from {{ ref('fct_financial_metrics') }}
    where period_label = 'instant' and period_instant = report_date
    group by cik, accession_number
), combined as (
    select
        p.*,
        b.total_assets,
        b.total_liabilities,
        b.cash_and_equivalents,
        -- Capital expenditures are reported as a positive payment, so FCF subtracts them.
        p.operating_cash_flow - p.capital_expenditures as free_cash_flow
    from periods as p
    left join balances as b
        on p.cik = b.cik and p.accession_number = b.accession_number
)
select
    c.cik,
    c.fiscal_year,
    c.fiscal_period,
    c.accession_number,
    c.form,
    c.filing_date,
    c.report_date,
    c.period_start,
    c.period_end,
    c.revenue,
    c.net_income,
    c.diluted_eps,
    c.operating_cash_flow,
    c.capital_expenditures,
    c.free_cash_flow,
    c.total_assets,
    c.total_liabilities,
    c.cash_and_equivalents,
    -- Ratios are floating point for analysis; the values above stay exact decimals.
    case when c.revenue > 0 then (c.net_income / c.revenue)::double end as net_margin,
    case when c.revenue > 0 then (c.free_cash_flow / c.revenue)::double end
        as free_cash_flow_margin,
    case when prior.revenue > 0 then (c.revenue / prior.revenue - 1)::double end
        as revenue_yoy_growth,
    c.derived_metric_codes
from combined as c
left join combined as prior
    on c.cik = prior.cik
    and c.fiscal_period = prior.fiscal_period
    and c.fiscal_year = prior.fiscal_year + 1
