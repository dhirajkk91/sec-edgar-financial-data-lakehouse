-- One row per company with the figures behind the dashboard's Insights view: revenue growth,
-- profitability, cash conversion and data coverage. Every input is an annual (FY) row of
-- fct_company_financials or a count from company_metric_coverage and metric_quality_issues.
-- Nothing here selects or derives a financial value, and a missing input leaves a NULL.
with annual as (
    select *
    from {{ ref('fct_company_financials') }}
    where fiscal_period = 'FY'
), revenue_years as (
    -- Growth runs from the first to the latest fiscal year with positive revenue.
    select
        cik,
        min(fiscal_year) as first_fiscal_year,
        max(fiscal_year) as latest_fiscal_year
    from annual
    where revenue > 0
    group by cik
), coverage as (
    select
        cik,
        sum(periods_present)::integer as metric_values_present,
        sum(periods_expected)::integer as metric_values_expected
    from {{ ref('company_metric_coverage') }}
    group by cik
), issues as (
    select
        cik,
        count(*) filter (where severity = 'ERROR')::integer as error_issue_count,
        count(*) filter (where severity = 'WARNING')::integer as warning_issue_count
    from {{ ref('metric_quality_issues') }}
    group by cik
)
select
    c.cik,
    y.first_fiscal_year,
    y.latest_fiscal_year,
    first_year.revenue as first_revenue,
    latest_year.revenue as latest_revenue,
    -- Compound annual growth rate. It needs at least two fiscal years to be meaningful.
    case
        when y.latest_fiscal_year > y.first_fiscal_year then
            power(
                (latest_year.revenue / first_year.revenue)::double,
                1.0 / (y.latest_fiscal_year - y.first_fiscal_year)
            ) - 1
    end as revenue_cagr,
    latest_year.net_income as latest_net_income,
    latest_year.free_cash_flow as latest_free_cash_flow,
    latest_year.net_margin as latest_net_margin,
    latest_year.free_cash_flow_margin as latest_free_cash_flow_margin,
    -- Free cash flow per dollar of net income; undefined when net income isn't positive.
    case
        when latest_year.net_income > 0 and latest_year.free_cash_flow is not null then
            (latest_year.free_cash_flow / latest_year.net_income)::double
    end as cash_conversion,
    coalesce(cov.metric_values_present, 0) as metric_values_present,
    coalesce(cov.metric_values_expected, 0) as metric_values_expected,
    case
        when cov.metric_values_expected > 0 then
            (cov.metric_values_present / cov.metric_values_expected)::double
    end as metric_coverage,
    coalesce(i.error_issue_count, 0) as error_issue_count,
    coalesce(i.warning_issue_count, 0) as warning_issue_count
from {{ ref('dim_companies') }} as c
left join revenue_years as y on c.cik = y.cik
left join annual as first_year
    on y.cik = first_year.cik and y.first_fiscal_year = first_year.fiscal_year
left join annual as latest_year
    on y.cik = latest_year.cik and y.latest_fiscal_year = latest_year.fiscal_year
left join coverage as cov on c.cik = cov.cik
left join issues as i on c.cik = i.cik
