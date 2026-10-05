with duplicates as (
    select cik, accession_number, metric_code
    from {{ ref('int_derived_q4_financial_metrics') }}
    group by cik, accession_number, metric_code
    having count(*) > 1
), invalid_periods as (
    select cik, accession_number, metric_code
    from {{ ref('int_derived_q4_financial_metrics') }}
    where fiscal_period_focus is distinct from 'Q4'
        or period_kind is distinct from 'duration'
        or period_label is distinct from 'derived_quarter'
        or value_origin is distinct from 'derived'
        or derivation_rule_version is distinct from '1'
        or derivation_method is distinct from 'ANNUAL_MINUS_Q3_YTD'
        or not coalesce(metric_code in ('revenue', 'net_income', 'operating_cash_flow', 'capital_expenditures')
            and form in ('10-K', '10-K/A')
            and unit_expression = '{http://www.xbrl.org/2003/iso4217}USD', false)
        or period_instant is not null
        or not coalesce(
            period_start <= period_end and period_end = report_date
            and date_diff('day', period_start, period_end) + 1 between 80 and 100,
            false)
)
select cik, accession_number, metric_code from duplicates
union all
select cik, accession_number, metric_code from invalid_periods
