with selected as (
    select * from {{ ref('int_reported_cash_flow_metrics') }}
), invalid_lists as (
    select source_occurrence_id
    from selected
    where supporting_occurrence_count is null
        or supporting_occurrence_count < 1
        or supporting_occurrence_ids is null
        or array_length(supporting_occurrence_ids) <> supporting_occurrence_count
        or list_unique(supporting_occurrence_ids) <> supporting_occurrence_count
        or not list_contains(supporting_occurrence_ids, source_occurrence_id)
), invalid_support as (
    select r.source_occurrence_id
    from selected as r
    cross join unnest(r.supporting_occurrence_ids) as support(id)
    left join {{ ref('int_financial_metric_candidates') }} as c
        on support.id = c.source_occurrence_id
    where c.source_occurrence_id is null
        or c.cik is distinct from r.cik
        or c.accession_number is distinct from r.accession_number
        or c.metric_code is distinct from r.metric_code
        or c.period_start is distinct from r.period_start
        or c.period_end is distinct from r.period_end
        or c.value_decimal is distinct from r.value_decimal
        or c.decimals is distinct from r.decimals
        or c.precision is distinct from r.precision
)
select source_occurrence_id from invalid_lists
union all
select source_occurrence_id from invalid_support
