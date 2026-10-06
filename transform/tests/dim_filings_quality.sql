with active_profiles as (
    select
        a.cik, a.accession_number, a.source_document_name, a.source_sha256,
        m.cik is not null as has_metadata, m.form, m.report_date
    from {{ ref('stg_silver_active_filings') }} as a
    left join {{ ref('stg_silver_filing_metadata') }} as m
        on a.cik = m.cik and a.accession_number = m.accession_number
), critical_metrics as (
    select metric_code from (values
        ('diluted_eps'), ('net_income'), ('revenue'), ('total_assets'), ('total_liabilities')) as required(metric_code)
), coverage as (
    select a.cik, a.accession_number, c.metric_code,
        exists (
            select 1 from {{ ref('fct_financial_metrics') }} as f
            where f.cik = a.cik and f.accession_number = a.accession_number
                and f.current_source_document_name = a.source_document_name
                and f.current_source_sha256 = a.source_sha256
                and f.metric_code = c.metric_code and f.value_origin = 'reported'
                and a.form in ('10-K', '10-K/A', '10-Q', '10-Q/A') and a.report_date is not null
                and (
                    (c.metric_code in ('revenue', 'net_income', 'diluted_eps') and f.period_end = a.report_date
                        and ((a.form in ('10-K', '10-K/A') and f.period_label = 'annual')
                            or (a.form in ('10-Q', '10-Q/A') and f.period_label = 'quarter')))
                    or (c.metric_code in ('total_assets', 'total_liabilities')
                        and f.period_label = 'instant' and f.period_instant = a.report_date)
                )
        ) as is_present
    from active_profiles as a
    cross join critical_metrics as c
), expected_coverage as (
    select
        cik, accession_number,
        count(*) filter (where is_present)::bigint as present_critical_metric_count,
        coalesce(list(metric_code order by metric_code) filter (where not is_present),
            []::varchar[]) as missing_critical_metric_codes
    from coverage
    group by cik, accession_number
), expected as (
    select
        a.cik, a.accession_number, c.present_critical_metric_count, c.missing_critical_metric_codes,
        case
            when not a.has_metadata then 'MISSING_FILING_METADATA'
            when a.report_date is null then 'MISSING_REPORT_DATE'
            when a.form is null or a.form not in ('10-K', '10-K/A', '10-Q', '10-Q/A') then 'UNSUPPORTED_FORM'
            when not exists (
                select 1 from {{ ref('fct_financial_metrics') }} as f
                where f.cik = a.cik and f.accession_number = a.accession_number
                    and f.current_source_document_name = a.source_document_name
                    and f.current_source_sha256 = a.source_sha256
            ) then 'NO_SELECTED_METRICS'
            when c.present_critical_metric_count < 5 then 'MISSING_CRITICAL_METRICS'
            when exists (
                select 1 from {{ ref('metric_quality_issues') }} as i
                where i.cik = a.cik and i.accession_number = a.accession_number and i.severity = 'ERROR'
            ) then 'METRIC_ERRORS'
            else 'CRITICAL_METRICS_PRESENT'
        end as gold_quality_reason
    from active_profiles as a
    inner join expected_coverage as c
        on a.cik = c.cik and a.accession_number = c.accession_number
)
select coalesce(e.cik, d.cik) as cik, coalesce(e.accession_number, d.accession_number) as accession_number
from expected as e
full outer join {{ ref('dim_filings') }} as d
    on e.cik = d.cik and e.accession_number = d.accession_number
where e.cik is null or d.cik is null
    or d.present_critical_metric_count is distinct from e.present_critical_metric_count
    or d.missing_critical_metric_codes is distinct from e.missing_critical_metric_codes
    or d.gold_quality_reason is distinct from e.gold_quality_reason
    or d.gold_quality_status is distinct from case
        when e.gold_quality_reason in ('MISSING_FILING_METADATA', 'MISSING_REPORT_DATE',
            'UNSUPPORTED_FORM', 'NO_SELECTED_METRICS') then 'FAILED'
        when e.gold_quality_reason in ('MISSING_CRITICAL_METRICS', 'METRIC_ERRORS') then 'PARTIAL'
        else 'COMPLETE' end
    or d.quality_rule_version is distinct from '1'
