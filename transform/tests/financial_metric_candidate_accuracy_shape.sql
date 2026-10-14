select source_occurrence_id
from {{ ref('int_financial_metric_candidate_accuracy') }}
where accuracy_is_usable is null
    or (accuracy_is_usable and (accuracy_issue_code is not null or accuracy_issue_details is not null))
    or (not accuracy_is_usable and (accuracy_issue_code is null or accuracy_issue_details is null
        or length(accuracy_issue_details) = 0))
