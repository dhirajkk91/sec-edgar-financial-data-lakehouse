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
    where c.metric_code = 'revenue'
        and m.form in ('10-K', '10-Q', '10-K/A', '10-Q/A')
        and m.report_date is not null
        and c.period_kind = 'duration'
        and c.period_start is not null
        and c.period_end is not null
        and c.period_start <= c.period_end
        and c.period_instant is null
        and c.period_end = m.report_date
        and c.dimension_count = 0
        and c.has_dimensions = false
        and c.is_usd = true
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
        period_start,
        period_end,
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
    group by cik, accession_number, metric_code, report_date, period_start, period_end
), agreeing_occurrences as (
    -- Agreement across all mapped concepts must hold before priority can select a row.
    select
        e.*,
        a.supporting_occurrence_count,
        a.supporting_occurrence_ids
    from eligible as e
    inner join period_agreement as a
        on e.cik = a.cik
        and e.accession_number = a.accession_number
        and e.metric_code = a.metric_code
        and e.period_start = a.period_start
        and e.period_end = a.period_end
    where a.value_count = 1 and a.decimals_count = 1 and a.precision_count = 1
), ranked as (
    select
        *,
        row_number() over (
            partition by cik, accession_number, metric_code, period_start, period_end
            order by concept_priority, source_ordinal, source_occurrence_id
        ) as representative_rank
    from agreeing_occurrences
)
select
    metric_code,
    concept_priority,
    cik,
    accession_number,
    source_occurrence_id,
    source_document_name,
    source_sha256,
    source_ordinal,
    processing_run_id,
    parser_version,
    schema_version,
    concept_namespace,
    concept_local_name,
    raw_value,
    value_decimal,
    decimals,
    precision,
    unit_ref,
    unit_expression,
    context_ref,
    entity_scheme,
    entity_identifier,
    period_kind,
    period_start,
    period_end,
    period_instant,
    dimension_count,
    has_dimensions,
    duration_days,
    is_usd,
    form,
    filing_date,
    report_date,
    metadata_run_id,
    submissions_sha256,
    'reported_duration'::varchar as period_label,
    '1'::varchar as selection_rule_version,
    case when supporting_occurrence_count = 1
        then 'SINGLE_ELIGIBLE_OCCURRENCE'
        else 'AGREEING_ELIGIBLE_OCCURRENCES'
    end as selection_reason,
    supporting_occurrence_count,
    supporting_occurrence_ids
from ranked
where representative_rank = 1
