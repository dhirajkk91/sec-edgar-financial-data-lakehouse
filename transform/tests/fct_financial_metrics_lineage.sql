with derived_inputs as (
    select
        cik, accession_number, metric_code, form,
        filing_date, report_date, fiscal_year_focus, fiscal_period_focus,
        period_kind, period_start, period_end, period_instant,
        period_label, value_origin, value_decimal, unit_expression,
        derivation_rule_version, derivation_method, current_source_occurrence_id, current_source_document_name,
        current_source_sha256, current_value_decimal, current_supporting_occurrence_ids, previous_accession_number,
        previous_source_occurrence_id, previous_source_document_name, previous_source_sha256, previous_value_decimal,
        previous_supporting_occurrence_ids, cumulative_period_start, previous_period_end, current_decimals,
        current_precision, previous_decimals, previous_precision
    from {{ ref('int_derived_quarterly_cash_flow') }}
    union all
    select
        cik, accession_number, metric_code, form,
        filing_date, report_date, fiscal_year_focus, fiscal_period_focus,
        period_kind, period_start, period_end, period_instant,
        period_label, value_origin, value_decimal, unit_expression,
        derivation_rule_version, derivation_method, current_source_occurrence_id, current_source_document_name,
        current_source_sha256, current_value_decimal, current_supporting_occurrence_ids, previous_accession_number,
        previous_source_occurrence_id, previous_source_document_name, previous_source_sha256, previous_value_decimal,
        previous_supporting_occurrence_ids, cumulative_period_start, previous_period_end, current_decimals,
        current_precision, previous_decimals, previous_precision
    from {{ ref('int_derived_q4_financial_metrics') }}
), invalid_reported as (
    select m.cik, m.accession_number, m.metric_code
    from {{ ref('fct_financial_metrics') }} as m
    left join {{ ref('int_reported_financial_periods') }} as r
        on m.cik = r.cik
        and m.accession_number = r.accession_number
        and m.metric_code = r.metric_code
        and m.period_kind is not distinct from r.period_kind
        and m.period_start is not distinct from r.period_start
        and m.period_end is not distinct from r.period_end
        and m.period_instant is not distinct from r.period_instant
        and m.current_source_occurrence_id = r.source_occurrence_id
    where m.value_origin = 'reported' and (
        r.source_occurrence_id is null
        or not coalesce(r.period_classification in ('annual', 'quarter', 'year_to_date', 'instant'), false)
        or m.cik is distinct from r.cik
        or m.accession_number is distinct from r.accession_number
        or m.metric_code is distinct from r.metric_code
        or m.form is distinct from r.form
        or m.filing_date is distinct from r.filing_date
        or m.report_date is distinct from r.report_date
        or m.fiscal_year_focus is distinct from r.fiscal_year_focus
        or m.fiscal_period_focus is distinct from r.fiscal_period_focus
        or m.period_kind is distinct from r.period_kind
        or m.period_start is distinct from r.period_start
        or m.period_end is distinct from r.period_end
        or m.period_instant is distinct from r.period_instant
        or m.period_label is distinct from r.period_classification
        or m.value_decimal is distinct from r.value_decimal
        or m.unit_expression is distinct from r.unit_expression
        or m.selection_rule_version is distinct from r.selection_rule_version
        or m.period_rule_version is distinct from r.period_rule_version
        or m.derivation_rule_version is distinct from null::varchar
        or m.derivation_method is distinct from null::varchar
        or m.concept_namespace is distinct from r.concept_namespace
        or m.concept_local_name is distinct from r.concept_local_name
        or m.raw_value is distinct from r.raw_value
        or m.current_source_occurrence_id is distinct from r.source_occurrence_id
        or m.current_source_document_name is distinct from r.source_document_name
        or m.current_source_sha256 is distinct from r.source_sha256
        or m.current_value_decimal is distinct from r.value_decimal
        or m.current_supporting_occurrence_ids is distinct from r.supporting_occurrence_ids
        or m.previous_accession_number is distinct from null::varchar
        or m.previous_source_occurrence_id is distinct from null::varchar
        or m.previous_source_document_name is distinct from null::varchar
        or m.previous_source_sha256 is distinct from null::varchar
        or m.previous_value_decimal is distinct from null::decimal(38,18)
        or m.previous_supporting_occurrence_ids is distinct from []::varchar[]
        or m.cumulative_period_start is distinct from null::date
        or m.previous_period_end is distinct from null::date
        or m.current_decimals is distinct from r.decimals
        or m.current_precision is distinct from r.precision
        or m.previous_decimals is distinct from null::varchar
        or m.previous_precision is distinct from null::varchar
        or m.selection_rule_version is null
        or m.concept_namespace is null
        or m.concept_local_name is null
        or m.raw_value is null
        or m.period_rule_version is null
    )
), invalid_derived as (
    select m.cik, m.accession_number, m.metric_code
    from {{ ref('fct_financial_metrics') }} as m
    left join derived_inputs as d
        on m.cik = d.cik
        and m.accession_number = d.accession_number
        and m.metric_code = d.metric_code
        and m.period_kind is not distinct from d.period_kind
        and m.period_start is not distinct from d.period_start
        and m.period_end is not distinct from d.period_end
        and m.period_instant is not distinct from d.period_instant
        and m.current_source_occurrence_id = d.current_source_occurrence_id
        and m.derivation_method = d.derivation_method
    where m.value_origin = 'derived' and (
        d.current_source_occurrence_id is null
        or m.cik is distinct from d.cik
        or m.accession_number is distinct from d.accession_number
        or m.metric_code is distinct from d.metric_code
        or m.form is distinct from d.form
        or m.filing_date is distinct from d.filing_date
        or m.report_date is distinct from d.report_date
        or m.fiscal_year_focus is distinct from d.fiscal_year_focus
        or m.fiscal_period_focus is distinct from d.fiscal_period_focus
        or m.period_kind is distinct from d.period_kind
        or m.period_start is distinct from d.period_start
        or m.period_end is distinct from d.period_end
        or m.period_instant is distinct from d.period_instant
        or m.period_label is distinct from d.period_label
        or m.value_origin is distinct from d.value_origin
        or m.value_decimal is distinct from d.value_decimal
        or m.unit_expression is distinct from d.unit_expression
        or m.selection_rule_version is not null
        or m.period_rule_version is not null
        or m.derivation_rule_version is distinct from d.derivation_rule_version
        or m.derivation_method is distinct from d.derivation_method
        or m.concept_namespace is not null
        or m.concept_local_name is not null
        or m.raw_value is not null
        or m.current_source_occurrence_id is distinct from d.current_source_occurrence_id
        or m.current_source_document_name is distinct from d.current_source_document_name
        or m.current_source_sha256 is distinct from d.current_source_sha256
        or m.current_value_decimal is distinct from d.current_value_decimal
        or m.current_supporting_occurrence_ids is distinct from d.current_supporting_occurrence_ids
        or m.previous_accession_number is distinct from d.previous_accession_number
        or m.previous_source_occurrence_id is distinct from d.previous_source_occurrence_id
        or m.previous_source_document_name is distinct from d.previous_source_document_name
        or m.previous_source_sha256 is distinct from d.previous_source_sha256
        or m.previous_value_decimal is distinct from d.previous_value_decimal
        or m.previous_supporting_occurrence_ids is distinct from d.previous_supporting_occurrence_ids
        or m.cumulative_period_start is distinct from d.cumulative_period_start
        or m.previous_period_end is distinct from d.previous_period_end
        or m.current_decimals is distinct from d.current_decimals
        or m.current_precision is distinct from d.current_precision
        or m.previous_decimals is distinct from d.previous_decimals
        or m.previous_precision is distinct from d.previous_precision
        or m.fiscal_year_focus is null
        or m.fiscal_period_focus is null
        or m.derivation_rule_version is null
        or m.derivation_method is null
        or m.previous_accession_number is null
        or m.previous_source_occurrence_id is null
        or m.previous_source_document_name is null
        or m.previous_source_sha256 is null
        or m.previous_value_decimal is null
        or m.cumulative_period_start is null
        or m.previous_period_end is null
    )
), invalid_shape as (
    select cik, accession_number, metric_code
    from {{ ref('fct_financial_metrics') }}
    where not coalesce(
        (period_kind = 'instant' and period_instant is not null
            and period_start is null and period_end is null and period_label = 'instant'
            and value_origin = 'reported')
        or (period_kind = 'duration' and period_start is not null and period_end is not null
            and period_instant is null and (
                (value_origin = 'reported' and period_label in ('annual', 'quarter', 'year_to_date'))
                or (value_origin = 'derived' and period_label = 'derived_quarter')
            )), false)
)
select cik, accession_number, metric_code from invalid_reported
union all
select cik, accession_number, metric_code from invalid_derived
union all
select cik, accession_number, metric_code from invalid_shape
