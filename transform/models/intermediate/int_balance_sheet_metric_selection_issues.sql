with eligible as (
    select
        c.*,
        m.form,
        m.filing_date,
        m.report_date,
        m.metadata_run_id,
        m.submissions_sha256
    from {{ ref('int_financial_metric_candidates') }} as c
    inner join {{ ref('stg_silver_filing_metadata') }} as m
        on c.cik = m.cik and c.accession_number = m.accession_number
    where c.metric_code in ('total_assets', 'total_liabilities', 'cash_and_equivalents')
        and m.form in ('10-K', '10-Q', '10-K/A', '10-Q/A')
        and m.report_date is not null
        and c.period_kind = 'instant'
        and c.period_instant is not null
        and c.period_instant = m.report_date
        and c.period_start is null
        and c.period_end is null
        and c.dimension_count = 0
        and c.has_dimensions = false
        and c.unit_expression = '{http://www.xbrl.org/2003/iso4217}USD'
        and c.value_decimal is not null
        and c.entity_scheme in ('http://www.sec.gov/CIK', 'https://www.sec.gov/CIK')
        and regexp_full_match(c.entity_identifier, '[0-9]{1,10}')
        and regexp_matches(c.entity_identifier, '[1-9]')
        and lpad(c.entity_identifier, 10, '0') = c.cik
), period_agreement as (
    select
        cik,
        accession_number,
        metric_code,
        report_date,
        period_instant,
        count(*) as supporting_occurrence_count,
        list(source_occurrence_id order by concept_priority, source_ordinal, source_occurrence_id)
            as supporting_occurrence_ids,
        count(distinct value_decimal) as value_count,
        -- Count null as a separate accuracy value without inventing a sentinel string.
        count(distinct decimals) + case when count(decimals) < count(*) then 1 else 0 end
            as decimals_count,
        count(distinct precision) + case when count(precision) < count(*) then 1 else 0 end
            as precision_count
    from eligible
    group by cik, accession_number, metric_code, report_date, period_instant
), metrics as (
    select metric_code
    from (values ('total_assets'), ('total_liabilities'), ('cash_and_equivalents')) as supported(metric_code)
), filing_base as (
    select
        a.cik,
        a.accession_number,
        metrics.metric_code,
        m.cik as metadata_cik,
        m.form,
        m.report_date
    from {{ ref('stg_silver_active_filings') }} as a
    cross join metrics
    left join {{ ref('stg_silver_filing_metadata') }} as m
        on a.cik = m.cik and a.accession_number = m.accession_number
), filing_problems as (
    select
        f.cik,
        f.accession_number,
        f.metric_code,
        f.report_date,
        case
            when f.metadata_cik is null then 'MISSING_FILING_METADATA'
            when f.form is null or f.form not in ('10-K', '10-Q', '10-K/A', '10-Q/A') then 'UNSUPPORTED_FORM'
            when f.report_date is null then 'MISSING_REPORT_DATE'
            when not exists (
                select 1 from period_agreement as a
                where a.cik = f.cik and a.accession_number = f.accession_number
                    and a.metric_code = f.metric_code
            ) then 'NO_ELIGIBLE_METRIC'
        end as issue_code
    from filing_base as f
), filing_issues as (
    select
        cik,
        accession_number,
        metric_code,
        issue_code,
        case when issue_code = 'UNSUPPORTED_FORM' then 'WARNING' else 'ERROR' end as severity,
        report_date,
        null::date as period_instant,
        []::varchar[] as source_occurrence_ids,
        'Metric ' || metric_code || ': ' || case issue_code
            when 'MISSING_FILING_METADATA' then 'No verified filing metadata is stored for this active filing.'
            when 'UNSUPPORTED_FORM' then 'Verified form is outside 10-K, 10-Q, 10-K/A and 10-Q/A.'
            when 'MISSING_REPORT_DATE' then 'Supported filing has no verified report date to align metric instants.'
            when 'NO_ELIGIBLE_METRIC' then 'No metric occurrence satisfies all eligibility checks for report date '
                || report_date::varchar || '.'
        end as details
    from filing_problems
    where issue_code is not null
), period_issues as (
    select
        cik,
        accession_number,
        metric_code,
        case when value_count > 1 then 'METRIC_VALUE_CONFLICT'
            else 'METRIC_ACCURACY_UNRESOLVED'
        end as issue_code,
        'ERROR'::varchar as severity,
        report_date,
        period_instant,
        supporting_occurrence_ids as source_occurrence_ids,
        'Metric ' || metric_code || ', reported instant ' || period_instant::varchar || ': '
            || case when value_count > 1
                then 'eligible metric occurrences disagree on the exact Decimal value; concept priority cannot resolve this conflict.'
                else 'eligible metric values agree, but decimals or precision attributes differ (including nulls); rounding intervals are unresolved.'
            end as details
    from period_agreement
    where value_count > 1 or decimals_count > 1 or precision_count > 1
)
select
    cik, accession_number, metric_code, issue_code, severity, report_date,
    period_instant, source_occurrence_ids, details
from filing_issues
union all
select
    cik, accession_number, metric_code, issue_code, severity, report_date,
    period_instant, source_occurrence_ids, details
from period_issues
