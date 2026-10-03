select r.source_occurrence_id
from {{ ref('int_reported_balance_sheet_metrics') }} as r
inner join {{ ref('int_balance_sheet_metric_selection_issues') }} as i
    on r.cik = i.cik
    and r.accession_number = i.accession_number
    and r.metric_code = i.metric_code
    and r.period_instant = i.period_instant
where i.issue_code in ('METRIC_VALUE_CONFLICT', 'METRIC_ACCURACY_UNRESOLVED')
