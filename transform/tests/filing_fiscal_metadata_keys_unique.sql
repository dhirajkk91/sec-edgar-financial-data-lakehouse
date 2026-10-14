-- Storage allows old source versions; the active view must still have only one row per filing.
with storage_duplicates as (
    select cik, accession_number
    from {{ source('silver', 'filing_fiscal_metadata') }}
    group by cik, accession_number, source_document_name, source_sha256, extraction_version
    having count(*) > 1
), active_duplicates as (
    select cik, accession_number
    from {{ ref('stg_silver_filing_fiscal_metadata') }}
    group by cik, accession_number
    having count(*) > 1
)
select * from storage_duplicates
union all
select * from active_duplicates
