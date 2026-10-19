-- One row per company: the newest saved SEC company profile plus its filing coverage.
-- Every field comes from the SEC submissions response saved during Bronze discovery.
-- Sector is the SEC's own industry office (ownerOrg, e.g. "06 Technology"), not a market
-- classification such as GICS, which is why NVIDIA is Manufacturing and Visa is Trade & Services.
with ranked as (
    select
        *,
        row_number() over (
            partition by cik
            order by submissions_retrieved_at desc, submissions_sha256
        ) as snapshot_rank,
        count(*) over (partition by cik) as profile_snapshot_count
    from {{ ref('stg_silver_company_profiles') }}
), coverage as (
    select
        cik,
        count(*)::bigint as filing_count,
        min(fiscal_year_focus) as first_fiscal_year,
        max(fiscal_year_focus) as last_fiscal_year,
        min(report_date) as first_report_date,
        max(report_date) as last_report_date
    from {{ ref('dim_filings') }}
    group by cik
)
select
    r.cik,
    r.company_name,
    -- SEC lists the main ticker first. An empty SEC list stays NULL; no ticker is guessed.
    r.tickers[1] as primary_ticker,
    r.tickers,
    r.exchanges,
    nullif(regexp_extract(r.owner_org, '^([0-9]{2}) ', 1), '') as sec_office_code,
    coalesce(nullif(regexp_extract(r.owner_org, '^[0-9]{2} (.+)$', 1), ''), r.owner_org)
        as sector,
    r.sic_code,
    r.sic_description as industry,
    r.fiscal_year_end,
    r.state_of_incorporation,
    r.filer_category,
    r.entity_type,
    coalesce(c.filing_count, 0) as filing_count,
    c.first_fiscal_year,
    c.last_fiscal_year,
    c.first_report_date,
    c.last_report_date,
    r.profile_run_id,
    r.submissions_retrieved_at as profile_retrieved_at,
    r.submissions_sha256 as profile_submissions_sha256,
    r.profile_snapshot_count
from ranked as r
left join coverage as c on r.cik = c.cik
where r.snapshot_rank = 1
