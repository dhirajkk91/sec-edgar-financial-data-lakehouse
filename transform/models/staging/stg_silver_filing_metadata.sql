select
    m.cik,
    m.accession_number,
    m.form,
    m.filing_date,
    m.report_date,
    m.primary_document,
    m.metadata_run_id,
    m.run_record_path,
    m.submissions_path,
    m.submissions_url,
    m.submissions_retrieved_at,
    m.submissions_size_bytes,
    m.submissions_sha256
from {{ source('silver', 'filing_metadata') }} as m
inner join {{ ref('stg_silver_active_filings') }} as a
    on m.cik = a.cik and m.accession_number = a.accession_number
