with derivation_evidence as (
    select
        'int_quarterly_cash_flow_derivation_issues'::varchar as source_model,
        cik, accession_number, metric_code, issue_code,
        current_source_occurrence_id, previous_source_occurrence_ids
    from {{ ref('int_quarterly_cash_flow_derivation_issues') }}
    union all
    select
        'int_q4_financial_metric_derivation_issues'::varchar as source_model,
        cik, accession_number, metric_code, issue_code,
        current_source_occurrence_id, previous_source_occurrence_ids
    from {{ ref('int_q4_financial_metric_derivation_issues') }}
)
select q.cik, q.accession_number, q.metric_code, q.issue_stage, q.source_model, q.issue_code
from {{ ref('metric_quality_issues') }} as q
where not coalesce(
    (q.issue_stage = 'selection'
        and q.source_model in ('int_revenue_selection_issues', 'int_income_metric_selection_issues',
            'int_balance_sheet_metric_selection_issues', 'int_cash_flow_metric_selection_issues')
        and q.fiscal_year_focus is null and q.fiscal_period_focus is null
        and q.current_source_occurrence_id is null
        and q.previous_source_occurrence_ids = []::varchar[]
        and (
            (q.source_model = 'int_balance_sheet_metric_selection_issues'
                and q.period_start is null and q.period_end is null)
            or (q.source_model <> 'int_balance_sheet_metric_selection_issues' and q.period_instant is null)
        ))
    or (q.issue_stage = 'period_classification'
        and q.source_model = 'int_reported_financial_periods'
        and q.current_source_occurrence_id is not null
        and q.previous_source_occurrence_ids = []::varchar[]
        -- Canonical balance-sheet codes follow the existing metric_concept_map seed.
        and q.severity = case when q.metric_code in ('revenue', 'net_income', 'diluted_eps', 'total_assets', 'total_liabilities')
            then 'ERROR' else 'WARNING' end)
    or (q.issue_stage = 'derivation'
        and q.source_model in ('int_quarterly_cash_flow_derivation_issues', 'int_q4_financial_metric_derivation_issues')
        and q.period_start is null and q.period_end is null and q.period_instant is null
        and q.current_source_occurrence_id is not null
        and q.source_occurrence_ids = [q.current_source_occurrence_id]::varchar[]), false)
    or (q.issue_stage = 'period_classification' and not exists (
        select 1
        from {{ ref('int_reported_financial_periods') }} as r
        where r.period_issue_code is not null
            and r.cik = q.cik and r.accession_number = q.accession_number and r.metric_code = q.metric_code
            and r.period_issue_code = q.issue_code
            and r.source_occurrence_id = q.current_source_occurrence_id
            and r.supporting_occurrence_ids is not distinct from q.source_occurrence_ids
            and r.report_date is not distinct from q.report_date
            and r.period_start is not distinct from q.period_start
            and r.period_end is not distinct from q.period_end
            and r.period_instant is not distinct from q.period_instant
            and r.fiscal_year_focus is not distinct from q.fiscal_year_focus
            and r.fiscal_period_focus is not distinct from q.fiscal_period_focus
            and r.period_classification_reason is not distinct from q.details
    ))
    or (q.issue_stage = 'derivation' and not exists (
        select 1 from derivation_evidence as d
        where d.source_model = q.source_model
            and d.cik = q.cik and d.accession_number = q.accession_number and d.metric_code = q.metric_code
            and d.issue_code = q.issue_code
            and d.current_source_occurrence_id = q.current_source_occurrence_id
            and d.previous_source_occurrence_ids is not distinct from q.previous_source_occurrence_ids
    ))
