select d.cik, d.accession_number, d.metric_code
from {{ ref('int_derived_quarterly_cash_flow') }} as d
left join {{ ref('int_reported_financial_periods') }} as c
    on c.cik = d.cik and c.accession_number = d.accession_number
    and c.metric_code = d.metric_code and c.source_occurrence_id = d.current_source_occurrence_id
left join {{ ref('int_reported_financial_periods') }} as p
    on p.cik = d.cik and p.accession_number = d.previous_accession_number
    and p.metric_code = d.metric_code and p.source_occurrence_id = d.previous_source_occurrence_id
where c.source_occurrence_id is null or p.source_occurrence_id is null
    or d.form is distinct from c.form
    or d.filing_date is distinct from c.filing_date
    or d.report_date is distinct from c.report_date
    or d.fiscal_year_focus is distinct from c.fiscal_year_focus
    or d.fiscal_period_focus is distinct from c.fiscal_period_focus
    or d.current_source_document_name is distinct from c.source_document_name
    or d.current_source_sha256 is distinct from c.source_sha256
    or d.current_value_decimal is distinct from c.value_decimal
    or d.current_supporting_occurrence_ids is distinct from c.supporting_occurrence_ids
    or d.previous_source_document_name is distinct from p.source_document_name
    or d.previous_source_sha256 is distinct from p.source_sha256
    or d.previous_value_decimal is distinct from p.value_decimal
    or d.previous_supporting_occurrence_ids is distinct from p.supporting_occurrence_ids
    or d.current_decimals is distinct from c.decimals
    or d.current_precision is distinct from c.precision
    or d.previous_decimals is distinct from p.decimals
    or d.previous_precision is distinct from p.precision
    or d.unit_expression is distinct from c.unit_expression
    or d.cumulative_period_start is distinct from c.period_start
    or d.cumulative_period_start is distinct from p.period_start
    or d.previous_period_end is distinct from p.period_end
    or d.period_start is distinct from p.period_end + 1
    or d.period_end is distinct from c.period_end
    or d.value_decimal is distinct from try(cast(c.value_decimal - p.value_decimal as decimal(38,18)))
    or not coalesce(
        c.period_classification = 'year_to_date' and c.form in ('10-Q', '10-Q/A')
        and c.value_decimal is not null and p.value_decimal is not null
        and c.unit_expression = '{http://www.xbrl.org/2003/iso4217}USD'
        and p.unit_expression = '{http://www.xbrl.org/2003/iso4217}USD'
        and c.dimension_count = 0 and c.has_dimensions = false
        and p.dimension_count = 0 and p.has_dimensions = false
        and c.period_kind = 'duration' and p.period_kind = 'duration'
        and c.period_start <= c.period_end and p.period_start <= p.period_end
        and c.period_instant is null and p.period_instant is null
        and c.period_end = c.report_date and p.period_end = p.report_date
        and p.form in ('10-Q', '10-Q/A')
        and c.period_start = p.period_start and p.period_end < c.period_end
        and c.cik = p.cik and c.metric_code = p.metric_code
        and c.fiscal_year_focus = p.fiscal_year_focus
        and c.accession_number <> p.accession_number
        and ((c.fiscal_period_focus = 'Q2' and p.fiscal_period_focus = 'Q1'
                and p.period_classification = 'quarter')
            or (c.fiscal_period_focus = 'Q3' and p.fiscal_period_focus = 'Q2'
                and p.period_classification = 'year_to_date')),
        false)
    or 1 <> (
        select count(*) from {{ ref('int_reported_financial_periods') }} as candidate
        where candidate.cik = c.cik and candidate.metric_code = c.metric_code
            and candidate.fiscal_year_focus = c.fiscal_year_focus
            and candidate.accession_number <> c.accession_number
            and ((c.fiscal_period_focus = 'Q2' and candidate.fiscal_period_focus = 'Q1'
                    and candidate.period_classification = 'quarter')
                or (c.fiscal_period_focus = 'Q3' and candidate.fiscal_period_focus = 'Q2'
                    and candidate.period_classification = 'year_to_date'))
    )
