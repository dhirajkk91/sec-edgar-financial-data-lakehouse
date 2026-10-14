-- Keep every candidate and its raw accuracy strings; only the appended fields decide usability.
-- XBRL 2.1 sections 4.6.3-4.6.6 and XML Schema integer/nonNegativeInteger define this gate.
with normalized as (
    select
        c.*,
        -- Trim XML whitespace only. Do not change the raw strings used for agreement.
        trim(decimals, chr(9) || chr(10) || chr(13) || ' ') as checked_decimals,
        trim(precision, chr(9) || chr(10) || chr(13) || ' ') as checked_precision
    from {{ ref('int_financial_metric_candidates') }} as c
), validated as (
    select
        *,
        case
            when decimals is null and precision is null then 'MISSING_ACCURACY'
            when decimals is not null and precision is not null then 'MULTIPLE_ACCURACY_ATTRIBUTES'
            when decimals is not null
                and not (checked_decimals = 'INF' or regexp_full_match(checked_decimals, '[+-]?[0-9]+'))
                then 'INVALID_DECIMALS'
            when precision is not null
                and not (checked_precision = 'INF' or regexp_full_match(checked_precision, '[+]?[0-9]+')
                    or regexp_full_match(checked_precision, '-0+'))
                then 'INVALID_PRECISION'
            -- Negative zero is valid nonNegativeInteger syntax, but still means unknown accuracy.
            when precision is not null and regexp_full_match(checked_precision, '[+-]?0+')
                then 'UNKNOWN_PRECISION'
        end::varchar as accuracy_issue_code
    from normalized
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
    accuracy_issue_code is null as accuracy_is_usable,
    accuracy_issue_code,
    case when accuracy_issue_code is not null then
        case accuracy_issue_code
            when 'MISSING_ACCURACY' then 'Exactly one accuracy attribute is required.'
            when 'MULTIPLE_ACCURACY_ATTRIBUTES' then 'Both accuracy attributes are present; exactly one is required.'
            when 'INVALID_DECIMALS' then 'Decimals must be an XML Schema integer or uppercase INF.'
            when 'INVALID_PRECISION' then 'Precision must be an XML Schema non-negative integer or uppercase INF.'
            when 'UNKNOWN_PRECISION' then 'Zero precision conveys unknown accuracy and cannot support trusted selection.'
        end || ' Raw attributes: decimals=' || coalesce(to_json(decimals)::varchar, 'null')
            || ', precision=' || coalesce(to_json(precision)::varchar, 'null') || '.'
    end::varchar as accuracy_issue_details
from validated
