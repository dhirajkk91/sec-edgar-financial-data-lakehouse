select cik, accession_number, issue_code
from {{ ref('int_cash_flow_metric_selection_issues') }}
where severity is distinct from (case when issue_code in ('MISSING_FILING_METADATA', 'MISSING_REPORT_DATE') then 'ERROR' else 'WARNING' end)
    or (
        issue_code in ('METRIC_VALUE_CONFLICT', 'METRIC_ACCURACY_UNRESOLVED')
        and (period_start is null or period_end is null or array_length(source_occurrence_ids) < 2)
    )
    or (
        issue_code in ('MISSING_FILING_METADATA', 'UNSUPPORTED_FORM', 'MISSING_REPORT_DATE', 'NO_ELIGIBLE_METRIC')
        and (period_start is not null or period_end is not null or array_length(source_occurrence_ids) <> 0)
    )
    or (list_count(source_occurrence_ids) <> array_length(source_occurrence_ids))
    or (list_unique(source_occurrence_ids) <> array_length(source_occurrence_ids))
    or (issue_code in ('METRIC_VALUE_CONFLICT', 'METRIC_ACCURACY_UNRESOLVED')
        and (report_date is null or period_end is distinct from report_date or period_start > period_end))
    or (issue_code = 'NO_ELIGIBLE_METRIC' and report_date is null)
    or (issue_code in ('MISSING_FILING_METADATA', 'MISSING_REPORT_DATE') and report_date is not null)
