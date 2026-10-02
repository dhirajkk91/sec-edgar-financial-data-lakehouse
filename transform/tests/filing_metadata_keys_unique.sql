select cik, accession_number
from {{ ref('stg_silver_filing_metadata') }}
group by cik, accession_number
having count(*) > 1
