-- Record when a lower-priority mapped concept reports a different value than the selected one.
-- The selected value stands: priority picks the concept. This WARNING keeps the disagreement
-- visible instead of hiding it, e.g. Walmart's total Revenues vs. contract revenue.
with selected as (
    select cik, accession_number, metric_code, report_date, period_start, period_end,
        period_instant, concept_priority, concept_local_name, value_decimal, unit_expression
    from {{ ref('int_reported_revenue') }}
    union all
    select cik, accession_number, metric_code, report_date, period_start, period_end,
        period_instant, concept_priority, concept_local_name, value_decimal, unit_expression
    from {{ ref('int_reported_income_metrics') }}
    union all
    select cik, accession_number, metric_code, report_date, period_start, period_end,
        period_instant, concept_priority, concept_local_name, value_decimal, unit_expression
    from {{ ref('int_reported_cash_flow_metrics') }}
    union all
    select cik, accession_number, metric_code, report_date, period_start, period_end,
        period_instant, concept_priority, concept_local_name, value_decimal, unit_expression
    from {{ ref('int_reported_balance_sheet_metrics') }}
), differing as (
    -- Same filing, metric, exact period and unit, company-wide, but a different value.
    select
        s.*,
        c.concept_local_name as other_concept,
        c.concept_priority as other_priority,
        c.value_decimal as other_value,
        c.source_ordinal as other_ordinal,
        c.source_occurrence_id as other_occurrence_id
    from selected as s
    inner join {{ ref('int_financial_metric_candidates') }} as c
        on c.cik = s.cik
        and c.accession_number = s.accession_number
        and c.metric_code = s.metric_code
        and c.period_start is not distinct from s.period_start
        and c.period_end is not distinct from s.period_end
        and c.period_instant is not distinct from s.period_instant
        and c.unit_expression = s.unit_expression
        and c.dimension_count = 0
        and lpad(c.entity_identifier, 10, '0') = c.cik
        and c.concept_priority > s.concept_priority
        and c.value_decimal <> s.value_decimal
)
select
    cik,
    accession_number,
    metric_code,
    'LOWER_PRIORITY_CONCEPT_DIFFERS'::varchar as issue_code,
    'WARNING'::varchar as severity,
    report_date,
    period_start,
    period_end,
    period_instant,
    list(other_occurrence_id order by other_priority, other_ordinal, other_occurrence_id)
        as source_occurrence_ids,
    'Selected ' || any_value(concept_local_name) || ' = ' || format('{}', any_value(value_decimal))
        || '; lower-priority concepts report a different value: '
        || string_agg(
            distinct other_concept || ' = ' || format('{}', other_value), '; '
            order by other_concept || ' = ' || format('{}', other_value)
        )
        || '.' as details
from differing
group by cik, accession_number, metric_code, report_date, period_start, period_end, period_instant
