-- Coverage counts must stay within bounds and match fct_company_financials: every company
-- period expects each metric once, except Q4 diluted EPS.
with periods as (
    select cik, count(*) as period_count, count(*) filter (where fiscal_period = 'Q4') as q4_count
    from {{ ref('fct_company_financials') }}
    group by cik
)
select 'bounds' as problem, c.cik, c.metric_code
from {{ ref('company_metric_coverage') }} as c
where c.periods_expected <= 0
    or c.periods_present < 0
    or c.periods_present > c.periods_expected
    or abs(c.coverage - c.periods_present / c.periods_expected) > 1e-12
union all
select 'expected_periods', c.cik, c.metric_code
from {{ ref('company_metric_coverage') }} as c
join periods as p on c.cik = p.cik
where c.periods_expected <> case
    when c.metric_code = 'diluted_eps' then p.period_count - p.q4_count
    else p.period_count
end
