-- The pivot keeps one value per metric, so each period must supply at most one standalone
-- value per metric, and all of a period's duration values must cover the same dates.
with standalone as (
    select *
    from {{ ref('fct_financial_metrics') }}
    where (fiscal_period_focus = 'FY' and period_label = 'annual')
        or (fiscal_period_focus in ('Q1', 'Q2', 'Q3', 'Q4')
            and period_label in ('quarter', 'derived_quarter'))
), duplicate_metrics as (
    select cik, fiscal_year_focus, fiscal_period_focus, accession_number, metric_code
    from standalone
    group by all
    having count(*) > 1
), mixed_dates as (
    select cik, fiscal_year_focus, fiscal_period_focus, accession_number, null as metric_code
    from standalone
    group by cik, fiscal_year_focus, fiscal_period_focus, accession_number
    having count(distinct period_start) > 1 or count(distinct period_end) > 1
)
select * from duplicate_metrics
union all
select * from mixed_dates
