with expected as (
    select
        cik, accession_number, metric_code,
        period_kind, period_start, period_end,
        period_instant, r.period_classification as period_label, 'reported'::varchar as value_origin,
        r.source_occurrence_id as current_source_occurrence_id, null::varchar as previous_source_occurrence_id, null::varchar as derivation_method
    from {{ ref('int_reported_financial_periods') }} as r
    where period_classification in ('annual', 'quarter', 'year_to_date', 'instant')
    union all
    select
        cik, accession_number, metric_code,
        period_kind, period_start, period_end,
        period_instant, period_label, value_origin,
        current_source_occurrence_id, previous_source_occurrence_id, derivation_method
    from {{ ref('int_derived_quarterly_cash_flow') }} as r
    union all
    select
        cik, accession_number, metric_code,
        period_kind, period_start, period_end,
        period_instant, period_label, value_origin,
        current_source_occurrence_id, previous_source_occurrence_id, derivation_method
    from {{ ref('int_derived_q4_financial_metrics') }} as r
), actual as (
    select
        cik, accession_number, metric_code, period_kind,
        period_start, period_end, period_instant, period_label,
        value_origin, current_source_occurrence_id, previous_source_occurrence_id, derivation_method
    from {{ ref('fct_financial_metrics') }}
), missing_rows as (
    select
        cik, accession_number, metric_code, period_kind,
        period_start, period_end, period_instant, period_label,
        value_origin, current_source_occurrence_id, previous_source_occurrence_id, derivation_method
    from expected
    except all
    select
        cik, accession_number, metric_code, period_kind,
        period_start, period_end, period_instant, period_label,
        value_origin, current_source_occurrence_id, previous_source_occurrence_id, derivation_method
    from actual
), extra_rows as (
    select
        cik, accession_number, metric_code, period_kind,
        period_start, period_end, period_instant, period_label,
        value_origin, current_source_occurrence_id, previous_source_occurrence_id, derivation_method
    from actual
    except all
    select
        cik, accession_number, metric_code, period_kind,
        period_start, period_end, period_instant, period_label,
        value_origin, current_source_occurrence_id, previous_source_occurrence_id, derivation_method
    from expected
)
select cik, accession_number, metric_code from missing_rows
union all
select cik, accession_number, metric_code from extra_rows
