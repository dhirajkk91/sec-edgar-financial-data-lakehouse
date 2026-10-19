-- Keep saved SEC company profile snapshots for companies that have active filings.
select
    p.cik,
    p.company_name,
    p.tickers,
    p.exchanges,
    p.sic_code,
    p.sic_description,
    p.owner_org,
    p.entity_type,
    p.filer_category,
    p.fiscal_year_end,
    p.state_of_incorporation,
    p.profile_run_id,
    p.run_record_path,
    p.submissions_path,
    p.submissions_url,
    p.submissions_retrieved_at,
    p.submissions_size_bytes,
    p.submissions_sha256
from {{ source('silver', 'company_profiles') }} as p
where p.cik in (select cik from {{ ref('stg_silver_active_filings') }})
