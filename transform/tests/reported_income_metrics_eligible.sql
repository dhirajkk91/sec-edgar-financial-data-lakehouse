select r.source_occurrence_id
from {{ ref('int_reported_income_metrics') }} as r
left join {{ ref('stg_silver_filing_metadata') }} as m
    on r.cik = m.cik and r.accession_number = m.accession_number
where not coalesce(
    r.metric_code in ('net_income', 'diluted_eps')
    and m.cik is not null
    and r.form = m.form
    and r.report_date = m.report_date
    and r.form in ('10-K', '10-Q', '10-K/A', '10-Q/A')
    and r.report_date is not null
    and r.period_kind = 'duration'
    and r.period_start is not null
    and r.period_end is not null
    and r.period_start <= r.period_end
    and r.period_instant is null
    and r.period_end = r.report_date
    and r.dimension_count = 0 and r.has_dimensions = false
    and ((r.metric_code = 'net_income' and r.unit_expression = '{http://www.xbrl.org/2003/iso4217}USD' and r.is_usd = true)
        or (r.metric_code = 'diluted_eps' and r.unit_expression = '({http://www.xbrl.org/2003/iso4217}USD) / ({http://www.xbrl.org/2003/instance}shares)' and r.is_usd = false))
    and r.value_decimal is not null
    and r.entity_scheme in ('http://www.sec.gov/CIK', 'https://www.sec.gov/CIK')
    and regexp_full_match(r.entity_identifier, '[0-9]{1,10}')
    and regexp_matches(r.entity_identifier, '[1-9]')
    and lpad(r.entity_identifier, 10, '0') = r.cik,
    false
)
