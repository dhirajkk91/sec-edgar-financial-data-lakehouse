with parsed as (
    select
        *,
        try_cast(occurrences_json as json) as occurrences,
        try_cast(issues_json as json) as issues
    from {{ ref('stg_silver_filing_fiscal_metadata') }}
)
select cik, accession_number
from parsed
where occurrences is null or issues is null
    or json_type(occurrences) is distinct from 'ARRAY'
    or json_type(issues) is distinct from 'ARRAY'
    or (fiscal_year_focus is not null and fiscal_year_focus not between 1 and 9999)
    or (document_period_end_date is not null and document_period_end_date is distinct from report_date)
    or (fiscal_period_focus is not null and (
        (form in ('10-K', '10-K/A') and fiscal_period_focus <> 'FY')
        or (form in ('10-Q', '10-Q/A') and fiscal_period_focus not in ('Q1', 'Q2', 'Q3'))
    ))
    or (extraction_status = 'COMPLETE' and (
        fiscal_year_focus is null or fiscal_period_focus is null
        or document_period_end_date is null or json_array_length(issues) <> 0
    ))
    or (extraction_status = 'PARTIAL' and
        fiscal_year_focus is not null and fiscal_period_focus is not null
        and document_period_end_date is not null and json_array_length(issues) = 0
    )
