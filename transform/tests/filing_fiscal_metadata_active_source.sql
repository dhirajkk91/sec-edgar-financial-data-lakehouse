select m.cik, m.accession_number
from {{ ref('stg_silver_filing_fiscal_metadata') }} as m
left join {{ ref('stg_silver_active_filings') }} as a
    on m.cik = a.cik
    and m.accession_number = a.accession_number
    and m.source_document_name = a.source_document_name
    and m.source_sha256 = a.source_sha256
where a.cik is null or m.extraction_version is distinct from '1'
