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
), attached as (
    select
        r.metric_code, r.concept_priority, r.cik, r.accession_number,
        r.source_occurrence_id, r.source_document_name, r.source_sha256, r.source_ordinal,
        r.processing_run_id, r.parser_version, r.schema_version, r.concept_namespace,
        r.concept_local_name, r.raw_value, r.value_decimal, r.decimals,
        r.precision, r.unit_ref, r.unit_expression, r.context_ref,
        r.entity_scheme, r.entity_identifier, r.period_kind, r.period_start,
        r.period_end, r.period_instant, r.dimension_count, r.has_dimensions,
        r.duration_days, r.is_usd, r.form, r.filing_date,
        r.report_date, r.metadata_run_id, r.submissions_sha256, r.period_label,
        r.selection_rule_version, r.selection_reason, r.supporting_occurrence_count, r.supporting_occurrence_ids,
        f.fiscal_year_focus,
        f.fiscal_period_focus,
        f.document_period_end_date,
        f.extraction_status as fiscal_metadata_status,
        f.extraction_version as fiscal_extraction_version,
        f.metadata_run_id as fiscal_metadata_run_id,
        f.submissions_sha256 as fiscal_submissions_sha256,
        f.cik is not null as has_fiscal_metadata,
        f.form is distinct from r.form
            or f.report_date is distinct from r.report_date
            or f.metadata_run_id is distinct from r.metadata_run_id
            or f.submissions_sha256 is distinct from r.submissions_sha256
            as fiscal_lineage_conflicts,
        concat_ws('; ',
            case when f.form is distinct from r.form
                then concat('form ', f.form, ' versus ', r.form) end,
            case when f.report_date is distinct from r.report_date
                then concat('report date ', f.report_date, ' versus ', r.report_date) end,
            case when f.metadata_run_id is distinct from r.metadata_run_id
                then concat('metadata run ', f.metadata_run_id, ' versus ', r.metadata_run_id) end,
            case when f.submissions_sha256 is distinct from r.submissions_sha256
                then concat('submissions checksum ', f.submissions_sha256, ' versus ', r.submissions_sha256) end
        ) as fiscal_lineage_detail,
        -- Classification uses actual boundaries, never the supplied duration_days.
        date_diff('day', r.period_start, r.period_end) + 1 as inclusive_days,
        case
            when r.period_kind = 'duration' then coalesce(
                r.period_start is not null and r.period_end is not null
                and r.period_start <= r.period_end and r.period_instant is null
                and r.period_end = r.report_date, false)
            when r.period_kind = 'instant' then coalesce(
                r.period_instant is not null and r.period_start is null
                and r.period_end is null and r.period_instant = r.report_date, false)
            else false
        end as valid_reported_period
    from selected as r
    left join {{ ref('stg_silver_filing_fiscal_metadata') }} as f
        on r.cik = f.cik
        and r.accession_number = f.accession_number
        and r.source_document_name = f.source_document_name
        and r.source_sha256 = f.source_sha256
), checked as (
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
        selection_rule_version, selection_reason, supporting_occurrence_count, supporting_occurrence_ids,
        fiscal_year_focus, fiscal_period_focus, document_period_end_date, fiscal_metadata_status,
        fiscal_extraction_version, fiscal_metadata_run_id, fiscal_submissions_sha256, inclusive_days,
        fiscal_lineage_detail,
        -- First applicable issue wins. A valid instant needs no resolved fiscal focus.
        case
            when not valid_reported_period then 'INVALID_REPORTED_PERIOD'
            when has_fiscal_metadata and fiscal_lineage_conflicts
                then 'FISCAL_METADATA_LINEAGE_MISMATCH'
            when period_kind = 'instant' then null
            when not has_fiscal_metadata then 'MISSING_FISCAL_METADATA'
            when fiscal_year_focus is null or fiscal_period_focus is null
                or document_period_end_date is null then 'UNRESOLVED_FISCAL_METADATA'
            when document_period_end_date <> report_date then 'FISCAL_REPORT_DATE_MISMATCH'
            when not coalesce(
                (form in ('10-K', '10-K/A') and fiscal_period_focus = 'FY')
                or (form in ('10-Q', '10-Q/A') and fiscal_period_focus in ('Q1', 'Q2', 'Q3')),
                false) then 'FISCAL_PERIOD_FORM_MISMATCH'
            when not (
                (fiscal_period_focus = 'FY' and inclusive_days between 350 and 380)
                or (fiscal_period_focus in ('Q1', 'Q2', 'Q3') and inclusive_days between 80 and 100)
                or (fiscal_period_focus = 'Q2' and inclusive_days between 170 and 200)
                or (fiscal_period_focus = 'Q3' and inclusive_days between 260 and 290)
            ) then 'UNSUPPORTED_DURATION'
            else null
        end::varchar as initial_issue_code
    from attached
), provisional as (
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
        selection_rule_version, selection_reason, supporting_occurrence_count, supporting_occurrence_ids,
        fiscal_year_focus, fiscal_period_focus, document_period_end_date, fiscal_metadata_status,
        fiscal_extraction_version, fiscal_metadata_run_id, fiscal_submissions_sha256, inclusive_days,
        fiscal_lineage_detail, initial_issue_code,
        case
            when initial_issue_code is not null then 'unclassified'
            when period_kind = 'instant' then 'instant'
            when fiscal_period_focus = 'FY' then 'annual'
            when inclusive_days between 80 and 100 then 'quarter'
            else 'year_to_date'
        end::varchar as provisional_classification
    from checked
), distinct_periods as (
    -- Deduplicate period identities for the competition count, never financial rows.
    select distinct
        cik, accession_number, metric_code, provisional_classification,
        period_kind, period_start, period_end, period_instant,
        case when period_kind = 'instant' then cast(period_instant as varchar)
            else concat(period_start, ' through ', period_end)
        end as boundaries
    from provisional
    where provisional_classification <> 'unclassified'
), competing_periods as (
    select
        cik, accession_number, metric_code, provisional_classification,
        string_agg(boundaries, '; ' order by boundaries) as competing_boundaries
    from distinct_periods
    group by cik, accession_number, metric_code, provisional_classification
    having count(*) > 1
)
select
    p.metric_code, p.concept_priority, p.cik, p.accession_number,
    p.source_occurrence_id, p.source_document_name, p.source_sha256, p.source_ordinal,
    p.processing_run_id, p.parser_version, p.schema_version, p.concept_namespace,
    p.concept_local_name, p.raw_value, p.value_decimal, p.decimals,
    p.precision, p.unit_ref, p.unit_expression, p.context_ref,
    p.entity_scheme, p.entity_identifier, p.period_kind, p.period_start,
    p.period_end, p.period_instant, p.dimension_count, p.has_dimensions,
    p.duration_days, p.is_usd, p.form, p.filing_date,
    p.report_date, p.metadata_run_id, p.submissions_sha256, p.period_label,
    p.selection_rule_version, p.selection_reason, p.supporting_occurrence_count, p.supporting_occurrence_ids,
    p.fiscal_year_focus, p.fiscal_period_focus, p.document_period_end_date, p.fiscal_metadata_status,
    p.fiscal_extraction_version, p.fiscal_metadata_run_id, p.fiscal_submissions_sha256,
    case when c.cik is not null then 'unclassified'
        else p.provisional_classification
    end::varchar as period_classification,
    '1'::varchar as period_rule_version,
    case
        when p.initial_issue_code = 'INVALID_REPORTED_PERIOD' then concat(
            'Invalid ', coalesce(p.period_kind, 'null'), ' structure or boundary; start=',
            coalesce(cast(p.period_start as varchar), 'null'), ', end=',
            coalesce(cast(p.period_end as varchar), 'null'), ', instant=',
            coalesce(cast(p.period_instant as varchar), 'null'), ', verified report date=',
            coalesce(cast(p.report_date as varchar), 'null'), '.')
        when p.initial_issue_code = 'FISCAL_METADATA_LINEAGE_MISMATCH'
            then concat('Fiscal metadata lineage conflicts: ', p.fiscal_lineage_detail, '.')
        when p.initial_issue_code = 'MISSING_FISCAL_METADATA'
            then 'No fiscal metadata matches the selected source document and checksum; duration classification requires it.'
        when p.initial_issue_code = 'UNRESOLVED_FISCAL_METADATA' then concat(
            'Duration classification requires resolved fiscal year, focus and document end; year=',
            coalesce(cast(p.fiscal_year_focus as varchar), 'null'), ', focus=',
            coalesce(p.fiscal_period_focus, 'null'), ', document end=',
            coalesce(cast(p.document_period_end_date as varchar), 'null'), '.')
        when p.initial_issue_code = 'FISCAL_REPORT_DATE_MISMATCH' then concat(
            'Fiscal document end ', p.document_period_end_date,
            ' differs from verified report date ', p.report_date, '.')
        when p.initial_issue_code = 'FISCAL_PERIOD_FORM_MISMATCH'
            then concat('Form ', p.form, ' disagrees with fiscal focus ', p.fiscal_period_focus, '.')
        when p.initial_issue_code = 'UNSUPPORTED_DURATION' then concat(
            'No V1 range for form ', p.form, ', fiscal focus ', p.fiscal_period_focus,
            ' and inclusive duration ', p.inclusive_days, ' days (', p.period_start,
            ' through ', p.period_end, ').')
        when c.cik is not null then concat(
            'Competing ', p.provisional_classification, ' periods for this filing and metric: ',
            c.competing_boundaries, '; fiscal focus ', coalesce(p.fiscal_period_focus, 'null'),
            ', this inclusive duration ', coalesce(cast(p.inclusive_days as varchar), 'null'), ' days.')
        when p.provisional_classification = 'instant'
            then concat('Instant ', p.period_instant, ' matches the verified report date; fiscal focus is not required.')
        else concat('V1 ', p.provisional_classification, ': form ', p.form,
            ', fiscal focus ', p.fiscal_period_focus, ', reported fiscal year ', p.fiscal_year_focus,
            ', inclusive duration ', p.inclusive_days, ' days (', p.period_start,
            ' through ', p.period_end, '); fiscal document end and verified report date agree.')
    end::varchar as period_classification_reason,
    case when c.cik is not null then 'AMBIGUOUS_REPORTED_PERIOD'
        else p.initial_issue_code
    end::varchar as period_issue_code
from provisional as p
left join competing_periods as c
    on p.cik = c.cik and p.accession_number = c.accession_number
    and p.metric_code = c.metric_code
    and p.provisional_classification = c.provisional_classification
