with facts as (
    select
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
        is_nil,
        decimals,
        precision,
        unit_ref,
        unit_expression,
        context_ref,
        entity_scheme,
        entity_identifier,
        -- Silver uses uppercase labels; the metric map uses lowercase labels.
        lower(period_kind) as period_kind,
        period_start,
        period_end,
        period_instant
    from {{ ref('stg_silver_facts') }}
), active_filings as (
    select
        cik,
        accession_number,
        source_document_name,
        source_sha256
    from {{ ref('stg_silver_active_filings') }}
), concept_map as (
    select
        metric_code,
        concept_namespace_prefix,
        concept_local_name,
        concept_priority,
        expected_period_kind
    from {{ ref('metric_concept_map') }}
), dimensions as (
    select
        source_occurrence_id,
        count(*) as dimension_count
    from {{ ref('stg_silver_fact_dimensions') }}
    group by source_occurrence_id
), matched_facts as (
    select
        m.metric_code,
        m.concept_priority,
        f.cik,
        f.accession_number,
        f.source_occurrence_id,
        f.source_document_name,
        f.source_sha256,
        f.source_ordinal,
        f.processing_run_id,
        f.parser_version,
        f.schema_version,
        f.concept_namespace,
        f.concept_local_name,
        f.raw_value,
        f.value_decimal,
        f.decimals,
        f.precision,
        f.unit_ref,
        f.unit_expression,
        f.context_ref,
        f.entity_scheme,
        f.entity_identifier,
        f.period_kind,
        f.period_start,
        f.period_end,
        f.period_instant
    from facts as f
    inner join active_filings as a
        on f.cik = a.cik
        and f.accession_number = a.accession_number
        and f.source_document_name = a.source_document_name
        and f.source_sha256 = a.source_sha256
    inner join concept_map as m
        on f.concept_local_name = m.concept_local_name
        and starts_with(f.concept_namespace, m.concept_namespace_prefix)
        and f.period_kind = m.expected_period_kind
    where f.is_nil = false
        and f.value_decimal is not null
)
select
    f.metric_code,
    f.concept_priority,
    f.cik,
    f.accession_number,
    f.source_occurrence_id,
    f.source_document_name,
    f.source_sha256,
    f.source_ordinal,
    f.processing_run_id,
    f.parser_version,
    f.schema_version,
    f.concept_namespace,
    f.concept_local_name,
    f.raw_value,
    f.value_decimal,
    f.decimals,
    f.precision,
    f.unit_ref,
    f.unit_expression,
    f.context_ref,
    f.entity_scheme,
    f.entity_identifier,
    f.period_kind,
    f.period_start,
    f.period_end,
    f.period_instant,
    coalesce(d.dimension_count, 0::bigint) as dimension_count,
    coalesce(d.dimension_count, 0::bigint) > 0 as has_dimensions,
    case
        when f.period_start is not null and f.period_end is not null
            then date_diff('day', f.period_start, f.period_end) + 1
    end as duration_days,
    f.unit_expression = '{http://www.xbrl.org/2003/iso4217}USD' as is_usd
from matched_facts as f
left join dimensions as d
    on f.source_occurrence_id = d.source_occurrence_id
