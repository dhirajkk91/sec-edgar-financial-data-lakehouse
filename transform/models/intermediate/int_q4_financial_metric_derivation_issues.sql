with targets as (
    select
        r.cik, r.accession_number, r.metric_code,
        r.fiscal_year_focus, r.fiscal_period_focus, r.form, r.report_date,
        r.source_occurrence_id, r.value_decimal, r.unit_expression,
        r.dimension_count, r.has_dimensions,
        r.period_kind, r.period_start, r.period_end, r.period_instant
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
), candidates as (
    -- Count fiscal-role candidates before checking units or cumulative boundaries.
    select
        t.cik, t.accession_number, t.metric_code, t.source_occurrence_id,
        p.accession_number as previous_accession_number,
        p.source_occurrence_id as previous_source_occurrence_id,
        p.form as previous_form, p.report_date as previous_report_date,
        p.value_decimal as previous_value_decimal,
        p.unit_expression as previous_unit_expression,
        p.dimension_count as previous_dimension_count,
        p.has_dimensions as previous_has_dimensions,
        p.period_kind as previous_period_kind,
        p.period_start as previous_period_start,
        p.period_end as previous_period_end,
        p.period_instant as previous_period_instant
    from targets as t
    inner join {{ ref('int_reported_financial_periods') }} as p
        on p.cik = t.cik and p.metric_code = t.metric_code
        and p.fiscal_year_focus = t.fiscal_year_focus
        and p.accession_number <> t.accession_number
        and p.fiscal_period_focus = 'Q3'
        and p.period_classification = 'year_to_date'
), candidate_counts as (
    select
        cik, accession_number, metric_code, source_occurrence_id,
        count(*) as candidate_count,
        list(previous_source_occurrence_id order by previous_accession_number, previous_source_occurrence_id)
            as previous_source_occurrence_ids,
        string_agg(concat(previous_accession_number, ' / ', previous_source_occurrence_id,
            ' (', previous_period_start, ' through ', previous_period_end,
            '; unit=', coalesce(previous_unit_expression, 'null'), ')'),
            '; ' order by previous_accession_number, previous_source_occurrence_id) as candidate_details
    from candidates
    group by cik, accession_number, metric_code, source_occurrence_id
), pairs as (
    select
        t.cik, t.accession_number, t.metric_code,
        t.fiscal_year_focus, t.fiscal_period_focus, t.report_date,
        t.source_occurrence_id, t.value_decimal, t.unit_expression,
        t.dimension_count, t.has_dimensions,
        t.period_kind, t.period_start, t.period_end, t.period_instant,
        coalesce(c.candidate_count, 0) as candidate_count,
        coalesce(c.previous_source_occurrence_ids, []::varchar[]) as previous_source_occurrence_ids,
        c.candidate_details,
        p.previous_accession_number, p.previous_form, p.previous_report_date,
        p.previous_value_decimal, p.previous_unit_expression,
        p.previous_dimension_count, p.previous_has_dimensions,
        p.previous_period_kind, p.previous_period_start, p.previous_period_end,
        p.previous_period_instant,
        p.previous_period_end + 1 as derived_period_start,
        date_diff('day', p.previous_period_end + 1, t.period_end) + 1 as derived_duration_days,
        try(cast(t.value_decimal - p.previous_value_decimal as decimal(38,18))) as derived_value_decimal
    from targets as t
    left join candidate_counts as c
        on t.cik = c.cik and t.accession_number = c.accession_number
        and t.metric_code = c.metric_code and t.source_occurrence_id = c.source_occurrence_id
    left join candidates as p
        on c.candidate_count = 1
        and t.cik = p.cik and t.accession_number = p.accession_number
        and t.metric_code = p.metric_code and t.source_occurrence_id = p.source_occurrence_id
), evaluated as (
    select
        cik, accession_number, metric_code,
        fiscal_year_focus, fiscal_period_focus, report_date,
        source_occurrence_id, value_decimal, unit_expression,
        dimension_count, has_dimensions, period_kind,
        period_start, period_end, period_instant,
        candidate_count, previous_source_occurrence_ids, candidate_details,
        previous_accession_number, previous_form, previous_report_date,
        previous_value_decimal, previous_unit_expression, previous_dimension_count,
        previous_has_dimensions, previous_period_kind, previous_period_start,
        previous_period_end, previous_period_instant, derived_period_start,
        derived_duration_days, derived_value_decimal,
        case
            when not coalesce(
                value_decimal is not null
                and unit_expression = '{http://www.xbrl.org/2003/iso4217}USD'
                and dimension_count = 0 and has_dimensions = false
                and period_kind = 'duration' and period_start is not null
                and period_end is not null and period_start <= period_end
                and period_instant is null and period_end = report_date,
                false) then 'CURRENT_INPUT_INELIGIBLE'
            when candidate_count = 0 then 'MISSING_PREVIOUS_CUMULATIVE'
            when candidate_count > 1 then 'AMBIGUOUS_PREVIOUS_CUMULATIVE'
            when not coalesce(
                previous_value_decimal is not null
                and previous_unit_expression = '{http://www.xbrl.org/2003/iso4217}USD'
                and previous_dimension_count = 0 and previous_has_dimensions = false
                and previous_period_kind = 'duration' and previous_period_start is not null
                and previous_period_end is not null and previous_period_start <= previous_period_end
                and previous_period_instant is null and previous_period_end = previous_report_date
                and previous_form in ('10-Q', '10-Q/A')
                and previous_period_start = period_start and previous_period_end < period_end,
                false) then 'INCOMPATIBLE_CUMULATIVE_INPUTS'
            when derived_duration_days not between 80 and 100 then 'UNSUPPORTED_DERIVED_DURATION'
            when derived_value_decimal is null then 'DERIVED_VALUE_OUT_OF_RANGE'
            else null
        end::varchar as issue_code
    from pairs
)
select
    cik, accession_number, metric_code, fiscal_year_focus,
    'Q4'::varchar as fiscal_period_focus, report_date,
    issue_code, 'WARNING'::varchar as severity,
    source_occurrence_id as current_source_occurrence_id,
    previous_source_occurrence_ids,
    case
        when issue_code = 'CURRENT_INPUT_INELIGIBLE' then concat(
            'Annual FY input cannot support Q4 subtraction: value=',
            coalesce(cast(value_decimal as varchar), 'null'), ', unit=', coalesce(unit_expression, 'null'),
            ', dimensions=', coalesce(cast(dimension_count as varchar), 'null'), '/',
            coalesce(cast(has_dimensions as varchar), 'null'), ', kind=', coalesce(period_kind, 'null'),
            ', start=', coalesce(cast(period_start as varchar), 'null'), ', end=',
            coalesce(cast(period_end as varchar), 'null'), ', instant=',
            coalesce(cast(period_instant as varchar), 'null'), ', report date=',
            coalesce(cast(report_date as varchar), 'null'), '; candidates=',
            coalesce(candidate_details, 'none'), '.')
        when issue_code = 'MISSING_PREVIOUS_CUMULATIVE' then concat(
            'No different-accession Q3 nine-month year-to-date',
            ' predecessor for CIK ', cik, ', metric ', metric_code, ', reported fiscal year ',
            fiscal_year_focus, '; annual FY period ',
            period_start, ' through ', period_end, '.')
        when issue_code = 'AMBIGUOUS_PREVIOUS_CUMULATIVE' then concat(
            candidate_count, ' Q3 year-to-date predecessor candidates for Q4',
            ' in reported fiscal year ', fiscal_year_focus,
            '; counted before compatibility checks: ', candidate_details, '.')
        when issue_code = 'INCOMPATIBLE_CUMULATIVE_INPUTS' then concat(
            'Unique predecessor ', previous_accession_number, ' is incompatible: current start/end=',
            period_start, '/', period_end, '; previous start/end=',
            coalesce(cast(previous_period_start as varchar), 'null'), '/',
            coalesce(cast(previous_period_end as varchar), 'null'), '; previous form/report date=',
            coalesce(previous_form, 'null'), '/', coalesce(cast(previous_report_date as varchar), 'null'),
            '; previous kind/instant=', coalesce(previous_period_kind, 'null'), '/',
            coalesce(cast(previous_period_instant as varchar), 'null'),
            '; current/previous unit=', unit_expression, '/', coalesce(previous_unit_expression, 'null'),
            '; previous dimensions=', coalesce(cast(previous_dimension_count as varchar), 'null'), '/',
            coalesce(cast(previous_has_dimensions as varchar), 'null'), '; previous value=',
            coalesce(cast(previous_value_decimal as varchar), 'null'),
            '. Require common cumulative start, earlier predecessor end, USD and valid dimensionless durations.')
        when issue_code = 'UNSUPPORTED_DERIVED_DURATION' then concat(
            'Derived Q4 period ', derived_period_start, ' through ', period_end,
            ' has ', derived_duration_days, ' inclusive days; V1 requires 80–100 days. Predecessor ',
            previous_accession_number, ' ends ', previous_period_end, '.')
        when issue_code = 'DERIVED_VALUE_OUT_OF_RANGE' then concat(
            'Annual value ', value_decimal, ' minus predecessor ', previous_accession_number,
            ' Q3 year-to-date value ', previous_value_decimal, ' cannot fit DECIMAL(38,18); derived period ',
            derived_period_start, ' through ', period_end, '.')
    end::varchar as details
from evaluated
where issue_code is not null
