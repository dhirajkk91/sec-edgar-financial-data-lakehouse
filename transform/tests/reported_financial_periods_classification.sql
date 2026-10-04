with periods as (
    select
        p.*,
        f.cik is not null as has_fiscal_metadata,
        f.form as fiscal_form,
        f.report_date as fiscal_report_date,
        f.metadata_run_id as staged_metadata_run_id,
        f.submissions_sha256 as staged_submissions_sha256,
        f.fiscal_year_focus as staged_year,
        f.fiscal_period_focus as staged_focus,
        f.document_period_end_date as staged_document_end,
        f.extraction_status as staged_status,
        f.extraction_version as staged_version,
        date_diff('day', p.period_start, p.period_end) + 1 as inclusive_days
    from {{ ref('int_reported_financial_periods') }} as p
    left join {{ ref('stg_silver_filing_fiscal_metadata') }} as f
        on p.cik = f.cik and p.accession_number = f.accession_number
        and p.source_document_name = f.source_document_name
        and p.source_sha256 = f.source_sha256
), invalid_rows as (
    select source_occurrence_id
    from periods
    where fiscal_year_focus is distinct from staged_year
        or fiscal_period_focus is distinct from staged_focus
        or document_period_end_date is distinct from staged_document_end
        or fiscal_metadata_status is distinct from staged_status
        or fiscal_extraction_version is distinct from staged_version
        or fiscal_metadata_run_id is distinct from staged_metadata_run_id
        or fiscal_submissions_sha256 is distinct from staged_submissions_sha256
        or period_rule_version is distinct from '1'
        or period_classification_reason is null
        or length(trim(period_classification_reason)) = 0
        or not coalesce(
            (period_classification = 'unclassified' and period_issue_code in (
                'INVALID_REPORTED_PERIOD', 'MISSING_FISCAL_METADATA',
                'FISCAL_METADATA_LINEAGE_MISMATCH', 'UNRESOLVED_FISCAL_METADATA',
                'FISCAL_REPORT_DATE_MISMATCH', 'FISCAL_PERIOD_FORM_MISMATCH',
                'UNSUPPORTED_DURATION', 'AMBIGUOUS_REPORTED_PERIOD'
            ))
            or (period_classification in ('annual', 'quarter', 'year_to_date', 'instant')
                and period_issue_code is null), false)
        or (period_classification <> 'unclassified' and not coalesce(
            -- Available fiscal lineage must agree even for an instant.
            (not has_fiscal_metadata or (
                fiscal_form = form and fiscal_report_date = report_date
                and staged_metadata_run_id = metadata_run_id
                and staged_submissions_sha256 = submissions_sha256
            ))
            and (
                (period_classification = 'instant' and period_kind = 'instant'
                    and period_instant = report_date
                    and period_start is null and period_end is null)
                or (period_kind = 'duration' and period_start is not null
                    and period_end is not null and period_start <= period_end
                    and period_instant is null and period_end = report_date
                    and has_fiscal_metadata and fiscal_year_focus is not null
                    and document_period_end_date = report_date
                    and (
                        (period_classification = 'annual' and form in ('10-K', '10-K/A')
                            and fiscal_period_focus = 'FY' and inclusive_days between 350 and 380)
                        or (period_classification = 'quarter' and form in ('10-Q', '10-Q/A')
                            and fiscal_period_focus in ('Q1', 'Q2', 'Q3')
                            and inclusive_days between 80 and 100)
                        or (period_classification = 'year_to_date' and form in ('10-Q', '10-Q/A')
                            and ((fiscal_period_focus = 'Q2' and inclusive_days between 170 and 200)
                                or (fiscal_period_focus = 'Q3' and inclusive_days between 260 and 290)))
                    )
                )
            ), false)
        )
), classified_periods as (
    select distinct
        cik, accession_number, metric_code, period_classification,
        period_kind, period_start, period_end, period_instant
    from periods
    where period_classification <> 'unclassified'
), competing_classifications as (
    select cik, accession_number, metric_code, period_classification
    from classified_periods
    group by cik, accession_number, metric_code, period_classification
    having count(*) > 1
)
select source_occurrence_id from invalid_rows
union all
select p.source_occurrence_id
from periods as p
inner join competing_classifications as c
    on p.cik = c.cik and p.accession_number = c.accession_number
    and p.metric_code = c.metric_code and p.period_classification = c.period_classification
