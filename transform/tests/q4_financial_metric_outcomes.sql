with targets as (
    select
        r.cik, r.accession_number, r.metric_code, r.source_occurrence_id,
        r.fiscal_year_focus, r.fiscal_period_focus, r.report_date
    from {{ ref('int_reported_financial_periods') }} as r
    where r.metric_code in ('revenue', 'net_income', 'operating_cash_flow', 'capital_expenditures')
        and r.period_classification = 'annual'
        and r.fiscal_period_focus = 'FY' and r.form in ('10-K', '10-K/A')
        and not exists (
            select 1 from {{ ref('int_reported_financial_periods') }} as q
            where q.cik = r.cik and q.accession_number = r.accession_number
                and q.metric_code = r.metric_code and q.period_classification = 'quarter'
        )
), outcomes as (
    select cik, accession_number, metric_code, current_source_occurrence_id
    from {{ ref('int_derived_q4_financial_metrics') }}
    union all
    select cik, accession_number, metric_code, current_source_occurrence_id
    from {{ ref('int_q4_financial_metric_derivation_issues') }}
), outcome_counts as (
    select cik, accession_number, metric_code, current_source_occurrence_id, count(*) as outcome_count
    from outcomes
    group by cik, accession_number, metric_code, current_source_occurrence_id
), invalid_accounting as (
    select
        coalesce(t.cik, o.cik) as cik,
        coalesce(t.accession_number, o.accession_number) as accession_number,
        coalesce(t.metric_code, o.metric_code) as metric_code
    from targets as t
    full outer join outcome_counts as o
        on t.cik = o.cik and t.accession_number = o.accession_number
        and t.metric_code = o.metric_code and t.source_occurrence_id = o.current_source_occurrence_id
    where t.source_occurrence_id is null or o.outcome_count is distinct from 1
), candidate_evidence as (
    select
        t.cik, t.accession_number, t.metric_code, t.source_occurrence_id,
        coalesce(list(p.source_occurrence_id order by p.accession_number, p.source_occurrence_id)
            filter (where p.source_occurrence_id is not null), []::varchar[]) as expected_ids
    from targets as t
    left join {{ ref('int_reported_financial_periods') }} as p
        on p.cik = t.cik and p.metric_code = t.metric_code
        and p.fiscal_year_focus = t.fiscal_year_focus and p.accession_number <> t.accession_number
        and p.fiscal_period_focus = 'Q3' and p.period_classification = 'year_to_date'
    group by t.cik, t.accession_number, t.metric_code, t.source_occurrence_id
), invalid_issues as (
    select i.cik, i.accession_number, i.metric_code
    from {{ ref('int_q4_financial_metric_derivation_issues') }} as i
    left join targets as t
        on i.cik = t.cik and i.accession_number = t.accession_number
        and i.metric_code = t.metric_code and i.current_source_occurrence_id = t.source_occurrence_id
    left join candidate_evidence as e
        on i.cik = e.cik and i.accession_number = e.accession_number
        and i.metric_code = e.metric_code and i.current_source_occurrence_id = e.source_occurrence_id
    where t.source_occurrence_id is null
        or i.fiscal_year_focus is distinct from t.fiscal_year_focus
        or i.fiscal_period_focus is distinct from 'Q4'
        or i.severity is distinct from 'WARNING'
        or i.report_date is distinct from t.report_date
        or i.previous_source_occurrence_ids is distinct from e.expected_ids
        or i.details is null or length(trim(i.details)) = 0
        or (i.issue_code = 'MISSING_PREVIOUS_CUMULATIVE' and array_length(e.expected_ids) <> 0)
        or (i.issue_code = 'AMBIGUOUS_PREVIOUS_CUMULATIVE' and array_length(e.expected_ids) <= 1)
        or (i.issue_code in ('INCOMPATIBLE_CUMULATIVE_INPUTS', 'UNSUPPORTED_DERIVED_DURATION',
            'DERIVED_VALUE_OUT_OF_RANGE') and array_length(e.expected_ids) <> 1)
)
select cik, accession_number, metric_code from invalid_accounting
union all
select cik, accession_number, metric_code from invalid_issues
