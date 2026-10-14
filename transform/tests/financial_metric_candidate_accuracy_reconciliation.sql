-- The helper may append a decision, but cannot lose, change or duplicate a candidate.
with missing_or_changed as (
    select metric_code, concept_priority, cik, accession_number, source_occurrence_id, source_document_name, source_sha256, source_ordinal, processing_run_id, parser_version, schema_version, concept_namespace, concept_local_name, raw_value, value_decimal, decimals, precision, unit_ref, unit_expression, context_ref, entity_scheme, entity_identifier, period_kind, period_start, period_end, period_instant, dimension_count, has_dimensions, duration_days, is_usd from {{ ref('int_financial_metric_candidates') }}
    except all
    select metric_code, concept_priority, cik, accession_number, source_occurrence_id, source_document_name, source_sha256, source_ordinal, processing_run_id, parser_version, schema_version, concept_namespace, concept_local_name, raw_value, value_decimal, decimals, precision, unit_ref, unit_expression, context_ref, entity_scheme, entity_identifier, period_kind, period_start, period_end, period_instant, dimension_count, has_dimensions, duration_days, is_usd from {{ ref('int_financial_metric_candidate_accuracy') }}
), extra_or_changed as (
    select metric_code, concept_priority, cik, accession_number, source_occurrence_id, source_document_name, source_sha256, source_ordinal, processing_run_id, parser_version, schema_version, concept_namespace, concept_local_name, raw_value, value_decimal, decimals, precision, unit_ref, unit_expression, context_ref, entity_scheme, entity_identifier, period_kind, period_start, period_end, period_instant, dimension_count, has_dimensions, duration_days, is_usd from {{ ref('int_financial_metric_candidate_accuracy') }}
    except all
    select metric_code, concept_priority, cik, accession_number, source_occurrence_id, source_document_name, source_sha256, source_ordinal, processing_run_id, parser_version, schema_version, concept_namespace, concept_local_name, raw_value, value_decimal, decimals, precision, unit_ref, unit_expression, context_ref, entity_scheme, entity_identifier, period_kind, period_start, period_end, period_instant, dimension_count, has_dimensions, duration_days, is_usd from {{ ref('int_financial_metric_candidates') }}
)
select * from missing_or_changed
union all
select * from extra_or_changed
