select cik, accession_number, issue_code
from {{ ref('int_income_metric_selection_issues') }}
where severity is distinct from (case when issue_code = 'UNSUPPORTED_FORM' then 'WARNING' else 'ERROR' end)
    or (
        issue_code in ('METRIC_VALUE_CONFLICT', 'METRIC_ACCURACY_UNRESOLVED')
        and (period_start is null or period_end is null or array_length(source_occurrence_ids) < 2)
    )
    or (
        issue_code in ('MISSING_FILING_METADATA', 'UNSUPPORTED_FORM', 'MISSING_REPORT_DATE', 'NO_ELIGIBLE_METRIC')
        and (period_start is not null or period_end is not null or array_length(source_occurrence_ids) <> 0)
    )
