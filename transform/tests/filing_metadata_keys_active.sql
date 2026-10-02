select m.cik, m.accession_number
from {{ ref('stg_silver_filing_metadata') }} as m
where not exists (
    select 1
    from {{ ref('stg_silver_active_filings') }} as a
    where a.cik = m.cik and a.accession_number = m.accession_number
)
