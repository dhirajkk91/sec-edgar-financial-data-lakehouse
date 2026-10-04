with selected as (
    select
        metric_code, concept_priority, cik, accession_number,
        source_occurrence_id, source_document_name, source_sha256, source_ordinal,
        processing_run_id, parser_version, schema_version, concept_namespace,
        concept_local_name, raw_value, value_decimal, decimals,
        precision, unit_ref, unit_expression, context_ref,
        entity_scheme, entity_identifier, period_kind, period_start,
        period_end, period_instant, dimension_count, has_dimensions,
        duration_days, is_usd, form, filing_date,
        report_date, metadata_run_id, submissions_sha256, period_label,
        selection_rule_version, selection_reason, supporting_occurrence_count, supporting_occurrence_ids
    from {{ ref('int_reported_revenue') }}
    union all
    select
        metric_code, concept_priority, cik, accession_number,
        source_occurrence_id, source_document_name, source_sha256, source_ordinal,
        processing_run_id, parser_version, schema_version, concept_namespace,
        concept_local_name, raw_value, value_decimal, decimals,
        precision, unit_ref, unit_expression, context_ref,
        entity_scheme, entity_identifier, period_kind, period_start,
        period_end, period_instant, dimension_count, has_dimensions,
        duration_days, is_usd, form, filing_date,
        report_date, metadata_run_id, submissions_sha256, period_label,
        selection_rule_version, selection_reason, supporting_occurrence_count, supporting_occurrence_ids
    from {{ ref('int_reported_income_metrics') }}
    union all
    select
        metric_code, concept_priority, cik, accession_number,
        source_occurrence_id, source_document_name, source_sha256, source_ordinal,
        processing_run_id, parser_version, schema_version, concept_namespace,
        concept_local_name, raw_value, value_decimal, decimals,
        precision, unit_ref, unit_expression, context_ref,
        entity_scheme, entity_identifier, period_kind, period_start,
        period_end, period_instant, dimension_count, has_dimensions,
        duration_days, is_usd, form, filing_date,
        report_date, metadata_run_id, submissions_sha256, period_label,
        selection_rule_version, selection_reason, supporting_occurrence_count, supporting_occurrence_ids
    from {{ ref('int_reported_balance_sheet_metrics') }}
    union all
    select
        metric_code, concept_priority, cik, accession_number,
        source_occurrence_id, source_document_name, source_sha256, source_ordinal,
        processing_run_id, parser_version, schema_version, concept_namespace,
        concept_local_name, raw_value, value_decimal, decimals,
        precision, unit_ref, unit_expression, context_ref,
        entity_scheme, entity_identifier, period_kind, period_start,
        period_end, period_instant, dimension_count, has_dimensions,
        duration_days, is_usd, form, filing_date,
        report_date, metadata_run_id, submissions_sha256, period_label,
        selection_rule_version, selection_reason, supporting_occurrence_count, supporting_occurrence_ids
    from {{ ref('int_reported_cash_flow_metrics') }}
), output as (
    select
        metric_code, concept_priority, cik, accession_number,
        source_occurrence_id, source_document_name, source_sha256, source_ordinal,
        processing_run_id, parser_version, schema_version, concept_namespace,
        concept_local_name, raw_value, value_decimal, decimals,
        precision, unit_ref, unit_expression, context_ref,
        entity_scheme, entity_identifier, period_kind, period_start,
        period_end, period_instant, dimension_count, has_dimensions,
        duration_days, is_usd, form, filing_date,
        report_date, metadata_run_id, submissions_sha256, period_label,
        selection_rule_version, selection_reason, supporting_occurrence_count, supporting_occurrence_ids
    from {{ ref('int_reported_financial_periods') }}
), missing_or_changed as (
    select * from selected
    except all
    select * from output
), extra_or_changed as (
    select * from output
    except all
    select * from selected
)
select * from missing_or_changed
union all
select * from extra_or_changed
