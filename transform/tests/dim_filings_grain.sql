with summary_counts as (
    select cik, accession_number, count(*) as row_count
    from {{ ref('dim_filings') }}
    group by cik, accession_number
)
select coalesce(a.cik, d.cik) as cik, coalesce(a.accession_number, d.accession_number) as accession_number
from {{ ref('stg_silver_active_filings') }} as a
full outer join summary_counts as d
    on a.cik = d.cik and a.accession_number = d.accession_number
where a.cik is null or d.row_count is distinct from 1
