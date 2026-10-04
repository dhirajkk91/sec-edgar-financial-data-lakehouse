select
    m.cik,
    m.accession_number,
    m.source_document_name,
    m.source_sha256,
    m.extraction_version,
    m.form,
    m.report_date,
    m.metadata_run_id,
    m.submissions_sha256,
    m.extraction_status,
    m.fiscal_year_focus,
    m.fiscal_period_focus,
    m.document_period_end_date,
    m.occurrences_json,
    m.issues_json
from {{ source('silver', 'filing_fiscal_metadata') }} as m
inner join {{ ref('stg_silver_active_filings') }} as a
    on m.cik = a.cik
    and m.accession_number = a.accession_number
    and m.source_document_name = a.source_document_name
    and m.source_sha256 = a.source_sha256
where m.extraction_version = '1'
