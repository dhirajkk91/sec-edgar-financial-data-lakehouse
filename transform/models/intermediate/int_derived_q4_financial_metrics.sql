with targets as (
    select
        r.cik, r.accession_number, r.metric_code, r.form, r.filing_date, r.report_date,
        r.fiscal_year_focus, r.fiscal_period_focus, r.period_start, r.period_end,
        r.value_decimal, r.unit_expression, r.source_occurrence_id,
        r.source_document_name, r.source_sha256, r.supporting_occurrence_ids,
        r.decimals, r.precision
    from {{ ref('int_reported_financial_periods') }} as r
    where r.metric_code in ('revenue', 'net_income', 'operating_cash_flow', 'capital_expenditures')
        and r.period_classification = 'annual'
        and r.fiscal_period_focus = 'FY'
        and r.form in ('10-K', '10-K/A')
        and not exists (
            select 1 from {{ ref('int_reported_financial_periods') }} as q
            where q.cik = r.cik and q.accession_number = r.accession_number
                and q.metric_code = r.metric_code and q.period_classification = 'quarter'
        )
)
select
    t.cik,
    t.accession_number,
    t.metric_code,
    t.form,
    t.filing_date,
    t.report_date,
    t.fiscal_year_focus,
    'Q4'::varchar as fiscal_period_focus,
    'duration'::varchar as period_kind,
    p.period_end + 1 as period_start,
    t.period_end,
    null::date as period_instant,
    'derived_quarter'::varchar as period_label,
    'derived'::varchar as value_origin,
    try(cast(t.value_decimal - p.value_decimal as decimal(38,18))) as value_decimal,
    t.unit_expression,
    '1'::varchar as derivation_rule_version,
    'ANNUAL_MINUS_Q3_YTD'::varchar as derivation_method,
    t.source_occurrence_id as current_source_occurrence_id,
    t.source_document_name as current_source_document_name,
    t.source_sha256 as current_source_sha256,
    t.value_decimal as current_value_decimal,
    t.supporting_occurrence_ids as current_supporting_occurrence_ids,
    p.accession_number as previous_accession_number,
    p.source_occurrence_id as previous_source_occurrence_id,
    p.source_document_name as previous_source_document_name,
    p.source_sha256 as previous_source_sha256,
    p.value_decimal as previous_value_decimal,
    p.supporting_occurrence_ids as previous_supporting_occurrence_ids,
    t.period_start as cumulative_period_start,
    p.period_end as previous_period_end,
    t.decimals as current_decimals,
    t.precision as current_precision,
    p.decimals as previous_decimals,
    p.precision as previous_precision
from targets as t
inner join {{ ref('int_reported_financial_periods') }} as p
    on p.cik = t.cik and p.metric_code = t.metric_code
    and p.fiscal_year_focus = t.fiscal_year_focus
    and p.accession_number <> t.accession_number
    and p.fiscal_period_focus = 'Q3'
    and p.period_classification = 'year_to_date'
-- The issue view counts every predecessor and owns the failure precedence.
left join {{ ref('int_q4_financial_metric_derivation_issues') }} as i
    on i.cik = t.cik and i.accession_number = t.accession_number
    and i.metric_code = t.metric_code and i.current_source_occurrence_id = t.source_occurrence_id
where i.cik is null
