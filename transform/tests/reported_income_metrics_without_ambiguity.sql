select r.source_occurrence_id
from {{ ref('int_reported_income_metrics') }} as r
inner join {{ ref('int_income_metric_selection_issues') }} as i
    on r.cik = i.cik
    and r.accession_number = i.accession_number
    and r.metric_code = i.metric_code
    and r.period_start = i.period_start
    and r.period_end = i.period_end
where i.issue_code in ('METRIC_VALUE_CONFLICT', 'METRIC_ACCURACY_UNRESOLVED')
