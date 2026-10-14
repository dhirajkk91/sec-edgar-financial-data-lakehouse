-- All eight selected metric paths must keep unusable supporting evidence out of Gold.
with selected as (
    select * from {{ ref('int_reported_revenue') }}
    union all
    select * from {{ ref('int_reported_income_metrics') }}
    union all
    select * from {{ ref('int_reported_balance_sheet_metrics') }}
    union all
    select * from {{ ref('int_reported_cash_flow_metrics') }}
)
select s.source_occurrence_id, supporting_id
from selected as s
cross join unnest(s.supporting_occurrence_ids) as supporting(supporting_id)
left join {{ ref('int_financial_metric_candidate_accuracy') }} as c
    on c.cik = s.cik and c.accession_number = s.accession_number
    and c.metric_code = s.metric_code and c.source_occurrence_id = supporting_id
where c.accuracy_is_usable is distinct from true
