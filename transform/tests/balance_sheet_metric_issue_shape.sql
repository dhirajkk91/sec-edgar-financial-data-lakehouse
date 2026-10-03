select cik, accession_number, metric_code, issue_code
from {{ ref('int_balance_sheet_metric_selection_issues') }}
where severity is distinct from (case when issue_code = 'UNSUPPORTED_FORM' then 'WARNING' else 'ERROR' end)
    or list_unique(source_occurrence_ids) <> array_length(source_occurrence_ids)
    or list_count(source_occurrence_ids) <> array_length(source_occurrence_ids)
    or length(details) = 0
    or (
        issue_code in ('METRIC_VALUE_CONFLICT', 'METRIC_ACCURACY_UNRESOLVED')
        and (period_instant is null or report_date is null
            or period_instant is distinct from report_date
            or array_length(source_occurrence_ids) < 2)
    )
    or (
        issue_code in ('MISSING_FILING_METADATA', 'UNSUPPORTED_FORM', 'MISSING_REPORT_DATE', 'NO_ELIGIBLE_METRIC')
        and (period_instant is not null or array_length(source_occurrence_ids) <> 0)
    )
    or (issue_code in ('MISSING_FILING_METADATA', 'MISSING_REPORT_DATE') and report_date is not null)
    or (issue_code = 'NO_ELIGIBLE_METRIC' and report_date is null)
