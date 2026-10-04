with duplicates as (
    select cik, accession_number, metric_code
    from {{ ref('int_derived_quarterly_cash_flow') }}
    group by cik, accession_number, metric_code
    having count(*) > 1
), invalid_periods as (
    select cik, accession_number, metric_code
    from {{ ref('int_derived_quarterly_cash_flow') }}
    where period_instant is not null
        or not coalesce(
            period_start <= period_end and period_end = report_date
            and date_diff('day', period_start, period_end) + 1 between 80 and 100,
            false)
)
select * from duplicates
union all
select * from invalid_periods
