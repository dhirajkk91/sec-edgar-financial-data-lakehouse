with metric_rows as (
    -- Match the active source before aggregation; stale source metrics never enter counts or coverage.
    select
        f.cik, f.accession_number, f.metric_code, f.value_origin,
        coalesce(
            m.form in ('10-K', '10-K/A', '10-Q', '10-Q/A') and m.report_date is not null
            and f.value_origin = 'reported' and (
                (f.metric_code in ('revenue', 'net_income', 'diluted_eps')
                    and f.period_end = m.report_date and (
                        (m.form in ('10-K', '10-K/A') and f.period_label = 'annual')
                        or (m.form in ('10-Q', '10-Q/A') and f.period_label = 'quarter')
                    ))
                or (f.metric_code in ('total_assets', 'total_liabilities')
                    and f.period_label = 'instant' and f.period_instant = m.report_date)
            ),
            false) as satisfies_critical_profile
    from {{ ref('fct_financial_metrics') }} as f
    inner join {{ ref('stg_silver_active_filings') }} as a
        on f.cik = a.cik and f.accession_number = a.accession_number
        and f.current_source_document_name = a.source_document_name
        and f.current_source_sha256 = a.source_sha256
    left join {{ ref('stg_silver_filing_metadata') }} as m
        on f.cik = m.cik and f.accession_number = m.accession_number
), metric_counts as (
    select
        cik, accession_number,
        count(*)::bigint as financial_metric_row_count,
        count(*) filter (where value_origin = 'reported')::bigint as reported_metric_row_count,
        count(*) filter (where value_origin = 'derived')::bigint as derived_metric_row_count,
        count(distinct metric_code)::bigint as distinct_metric_count,
        count(distinct metric_code) filter (where satisfies_critical_profile)::bigint as present_critical_metric_count,
        coalesce(list(distinct metric_code order by metric_code) filter (where satisfies_critical_profile),
            []::varchar[]) as present_critical_metric_codes
    from metric_rows
    group by cik, accession_number
), issue_counts as (
    -- Preserve issue occurrences, including duplicates; aggregate independently of financial rows.
    select
        cik, accession_number,
        count(*)::bigint as quality_issue_count,
        count(*) filter (where severity = 'ERROR')::bigint as error_issue_count,
        count(*) filter (where severity = 'WARNING')::bigint as warning_issue_count
    from {{ ref('metric_quality_issues') }}
    group by cik, accession_number
), attached as (
    select
        a.cik as cik,
        a.accession_number as accession_number,
        m.form as form,
        m.filing_date as filing_date,
        m.report_date as report_date,
        m.primary_document as primary_document,
        ends_with(m.form, '/A') as is_amendment,
        f.fiscal_year_focus as fiscal_year_focus,
        f.fiscal_period_focus as fiscal_period_focus,
        f.document_period_end_date as document_period_end_date,
        f.extraction_status as fiscal_metadata_status,
        f.extraction_version as fiscal_extraction_version,
        m.metadata_run_id as metadata_run_id,
        m.submissions_sha256 as submissions_sha256,
        a.source_document_name as source_document_name,
        a.source_sha256 as source_sha256,
        a.silver_status as silver_status,
        a.parser_version as parser_version,
        a.schema_version as schema_version,
        a.activation_run_id as activation_run_id,
        a.activated_at as activated_at,
        a.version_path as version_path,
        a.publication_path as publication_path,
        a.publication_sha256 as publication_sha256,
        coalesce(a.accepted_count, 0::bigint) as accepted_fact_count,
        coalesce(a.dimension_count, 0::bigint) as dimension_count,
        coalesce(a.rejected_count, 0::bigint) as rejected_fact_count,
        coalesce(a.candidate_count, 0::bigint) as candidate_fact_count,
        coalesce(mc.financial_metric_row_count, 0::bigint) as financial_metric_row_count,
        coalesce(mc.reported_metric_row_count, 0::bigint) as reported_metric_row_count,
        coalesce(mc.derived_metric_row_count, 0::bigint) as derived_metric_row_count,
        coalesce(mc.distinct_metric_count, 0::bigint) as distinct_metric_count,
        coalesce(mc.present_critical_metric_count, 0::bigint) as present_critical_metric_count,
        list_filter(['diluted_eps', 'net_income', 'revenue', 'total_assets', 'total_liabilities']::varchar[],
            code -> not list_contains(coalesce(mc.present_critical_metric_codes, []::varchar[]), code)) as missing_critical_metric_codes,
        coalesce(ic.quality_issue_count, 0::bigint) as quality_issue_count,
        coalesce(ic.error_issue_count, 0::bigint) as error_issue_count,
        coalesce(ic.warning_issue_count, 0::bigint) as warning_issue_count,
        m.cik is not null as has_filing_metadata
    from {{ ref('stg_silver_active_filings') }} as a
    left join {{ ref('stg_silver_filing_metadata') }} as m
        on a.cik = m.cik and a.accession_number = m.accession_number
    -- The staging view already owns supported fiscal extraction-version filtering.
    left join {{ ref('stg_silver_filing_fiscal_metadata') }} as f
        on f.cik = a.cik and f.accession_number = a.accession_number
        and f.source_document_name = a.source_document_name and f.source_sha256 = a.source_sha256
        and f.form = m.form and f.report_date = m.report_date
        and f.metadata_run_id = m.metadata_run_id and f.submissions_sha256 = m.submissions_sha256
    left join metric_counts as mc
        on mc.cik = a.cik and mc.accession_number = a.accession_number
    left join issue_counts as ic
        on ic.cik = a.cik and ic.accession_number = a.accession_number
), assessed as (
    select
        cik,
        accession_number,
        form,
        filing_date,
        report_date,
        primary_document,
        is_amendment,
        fiscal_year_focus,
        fiscal_period_focus,
        document_period_end_date,
        fiscal_metadata_status,
        fiscal_extraction_version,
        metadata_run_id,
        submissions_sha256,
        source_document_name,
        source_sha256,
        silver_status,
        parser_version,
        schema_version,
        activation_run_id,
        activated_at,
        version_path,
        publication_path,
        publication_sha256,
        accepted_fact_count,
        dimension_count,
        rejected_fact_count,
        candidate_fact_count,
        financial_metric_row_count,
        reported_metric_row_count,
        derived_metric_row_count,
        distinct_metric_count,
        present_critical_metric_count,
        missing_critical_metric_codes,
        quality_issue_count,
        error_issue_count,
        warning_issue_count,
        case
            when not has_filing_metadata then 'MISSING_FILING_METADATA'
            when report_date is null then 'MISSING_REPORT_DATE'
            when form is null or form not in ('10-K', '10-K/A', '10-Q', '10-Q/A') then 'UNSUPPORTED_FORM'
            when financial_metric_row_count = 0 then 'NO_SELECTED_METRICS'
            when array_length(missing_critical_metric_codes) > 0 then 'MISSING_CRITICAL_METRICS'
            when error_issue_count > 0 then 'METRIC_ERRORS'
            else 'CRITICAL_METRICS_PRESENT'
        end::varchar as gold_quality_reason
    from attached
)
select
    cik,
    accession_number,
    form,
    filing_date,
    report_date,
    primary_document,
    is_amendment,
    fiscal_year_focus,
    fiscal_period_focus,
    document_period_end_date,
    fiscal_metadata_status,
    fiscal_extraction_version,
    metadata_run_id,
    submissions_sha256,
    source_document_name,
    source_sha256,
    silver_status,
    parser_version,
    schema_version,
    activation_run_id,
    activated_at,
    version_path,
    publication_path,
    publication_sha256,
    accepted_fact_count,
    dimension_count,
    rejected_fact_count,
    candidate_fact_count,
    financial_metric_row_count,
    reported_metric_row_count,
    derived_metric_row_count,
    distinct_metric_count,
    present_critical_metric_count,
    missing_critical_metric_codes,
    quality_issue_count,
    error_issue_count,
    warning_issue_count,
    case
        when gold_quality_reason in ('MISSING_FILING_METADATA', 'MISSING_REPORT_DATE',
            'UNSUPPORTED_FORM', 'NO_SELECTED_METRICS') then 'FAILED'
        when gold_quality_reason in ('MISSING_CRITICAL_METRICS', 'METRIC_ERRORS') then 'PARTIAL'
        else 'COMPLETE'
    end::varchar as gold_quality_status,
    gold_quality_reason,
    '1'::varchar as quality_rule_version
from assessed
