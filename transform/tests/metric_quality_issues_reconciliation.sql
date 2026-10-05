-- Compare complete mapped records in both directions, retaining repeated issue occurrences.
with expected as (
    select
        r.cik,
        r.accession_number,
        r.metric_code,
        'selection'::varchar as issue_stage,
        'int_revenue_selection_issues'::varchar as source_model,
        r.issue_code,
        r.severity,
        r.report_date,
        r.period_start,
        r.period_end,
        null::date as period_instant,
        null::integer as fiscal_year_focus,
        null::varchar as fiscal_period_focus,
        null::varchar as current_source_occurrence_id,
        r.source_occurrence_ids,
        []::varchar[] as previous_source_occurrence_ids,
        r.details
    from {{ ref('int_revenue_selection_issues') }} as r
    union all
    select
        r.cik,
        r.accession_number,
        r.metric_code,
        'selection'::varchar as issue_stage,
        'int_income_metric_selection_issues'::varchar as source_model,
        r.issue_code,
        r.severity,
        r.report_date,
        r.period_start,
        r.period_end,
        null::date as period_instant,
        null::integer as fiscal_year_focus,
        null::varchar as fiscal_period_focus,
        null::varchar as current_source_occurrence_id,
        r.source_occurrence_ids,
        []::varchar[] as previous_source_occurrence_ids,
        r.details
    from {{ ref('int_income_metric_selection_issues') }} as r
    union all
    select
        r.cik,
        r.accession_number,
        r.metric_code,
        'selection'::varchar as issue_stage,
        'int_balance_sheet_metric_selection_issues'::varchar as source_model,
        r.issue_code,
        r.severity,
        r.report_date,
        null::date as period_start,
        null::date as period_end,
        r.period_instant,
        null::integer as fiscal_year_focus,
        null::varchar as fiscal_period_focus,
        null::varchar as current_source_occurrence_id,
        r.source_occurrence_ids,
        []::varchar[] as previous_source_occurrence_ids,
        r.details
    from {{ ref('int_balance_sheet_metric_selection_issues') }} as r
    union all
    select
        r.cik,
        r.accession_number,
        r.metric_code,
        'selection'::varchar as issue_stage,
        'int_cash_flow_metric_selection_issues'::varchar as source_model,
        r.issue_code,
        r.severity,
        r.report_date,
        r.period_start,
        r.period_end,
        null::date as period_instant,
        null::integer as fiscal_year_focus,
        null::varchar as fiscal_period_focus,
        null::varchar as current_source_occurrence_id,
        r.source_occurrence_ids,
        []::varchar[] as previous_source_occurrence_ids,
        r.details
    from {{ ref('int_cash_flow_metric_selection_issues') }} as r
    union all
    select
        r.cik,
        r.accession_number,
        r.metric_code,
        'period_classification'::varchar as issue_stage,
        'int_reported_financial_periods'::varchar as source_model,
        r.period_issue_code as issue_code,
        case when r.metric_code in ('revenue', 'net_income', 'diluted_eps', 'total_assets', 'total_liabilities')
            then 'ERROR' else 'WARNING' end::varchar as severity,
        r.report_date,
        r.period_start,
        r.period_end,
        r.period_instant,
        r.fiscal_year_focus,
        r.fiscal_period_focus,
        r.source_occurrence_id as current_source_occurrence_id,
        r.supporting_occurrence_ids as source_occurrence_ids,
        []::varchar[] as previous_source_occurrence_ids,
        r.period_classification_reason as details
    from {{ ref('int_reported_financial_periods') }} as r
    where r.period_issue_code is not null
    union all
    select
        r.cik,
        r.accession_number,
        r.metric_code,
        'derivation'::varchar as issue_stage,
        'int_quarterly_cash_flow_derivation_issues'::varchar as source_model,
        r.issue_code,
        r.severity,
        r.report_date,
        null::date as period_start,
        null::date as period_end,
        null::date as period_instant,
        r.fiscal_year_focus,
        r.fiscal_period_focus,
        r.current_source_occurrence_id,
        [r.current_source_occurrence_id]::varchar[] as source_occurrence_ids,
        r.previous_source_occurrence_ids,
        r.details
    from {{ ref('int_quarterly_cash_flow_derivation_issues') }} as r
    union all
    select
        r.cik,
        r.accession_number,
        r.metric_code,
        'derivation'::varchar as issue_stage,
        'int_q4_financial_metric_derivation_issues'::varchar as source_model,
        r.issue_code,
        r.severity,
        r.report_date,
        null::date as period_start,
        null::date as period_end,
        null::date as period_instant,
        r.fiscal_year_focus,
        r.fiscal_period_focus,
        r.current_source_occurrence_id,
        [r.current_source_occurrence_id]::varchar[] as source_occurrence_ids,
        r.previous_source_occurrence_ids,
        r.details
    from {{ ref('int_q4_financial_metric_derivation_issues') }} as r
), actual as (
    select
        cik, accession_number, metric_code, issue_stage,
        source_model, issue_code, severity, report_date,
        period_start, period_end, period_instant, fiscal_year_focus,
        fiscal_period_focus, current_source_occurrence_id, source_occurrence_ids, previous_source_occurrence_ids,
        details
    from {{ ref('metric_quality_issues') }}
), missing_rows as (
    select
        cik, accession_number, metric_code, issue_stage,
        source_model, issue_code, severity, report_date,
        period_start, period_end, period_instant, fiscal_year_focus,
        fiscal_period_focus, current_source_occurrence_id, source_occurrence_ids, previous_source_occurrence_ids,
        details
    from expected
    except all
    select
        cik, accession_number, metric_code, issue_stage,
        source_model, issue_code, severity, report_date,
        period_start, period_end, period_instant, fiscal_year_focus,
        fiscal_period_focus, current_source_occurrence_id, source_occurrence_ids, previous_source_occurrence_ids,
        details
    from actual
), extra_rows as (
    select
        cik, accession_number, metric_code, issue_stage,
        source_model, issue_code, severity, report_date,
        period_start, period_end, period_instant, fiscal_year_focus,
        fiscal_period_focus, current_source_occurrence_id, source_occurrence_ids, previous_source_occurrence_ids,
        details
    from actual
    except all
    select
        cik, accession_number, metric_code, issue_stage,
        source_model, issue_code, severity, report_date,
        period_start, period_end, period_instant, fiscal_year_focus,
        fiscal_period_focus, current_source_occurrence_id, source_occurrence_ids, previous_source_occurrence_ids,
        details
    from expected
)
select cik, accession_number, metric_code, issue_stage, source_model, issue_code from missing_rows
union all
select cik, accession_number, metric_code, issue_stage, source_model, issue_code from extra_rows
