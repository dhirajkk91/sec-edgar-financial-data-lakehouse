select
    cik,
    accession_number,
    source_document_name,
    source_sha256,
    silver_status,
    parser_version,
    schema_version,
    activation_run_id,
    activated_at,
    version_path,
    publication_path,
    publication_sha256,
    accepted_count,
    dimension_count,
    rejected_count,
    candidate_count
from {{ source('silver', 'active_filings') }}
