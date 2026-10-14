-- Synthetic filing identity for accuracy-group regressions.
select
    cik::VARCHAR as cik,
    accession_number::VARCHAR as accession_number,
    source_document_name::VARCHAR as source_document_name,
    source_sha256::VARCHAR as source_sha256,
    silver_status::VARCHAR as silver_status,
    parser_version::VARCHAR as parser_version,
    schema_version::VARCHAR as schema_version,
    activation_run_id::VARCHAR as activation_run_id,
    activated_at::TIMESTAMP WITH TIME ZONE as activated_at,
    version_path::VARCHAR as version_path,
    publication_path::VARCHAR as publication_path,
    publication_sha256::VARCHAR as publication_sha256,
    accepted_count::BIGINT as accepted_count,
    dimension_count::BIGINT as dimension_count,
    rejected_count::BIGINT as rejected_count,
    candidate_count::BIGINT as candidate_count
from (values
    ('0000320193', '0000320193-25-000001', 'filing.xml', 'checksum', 'COMPLETE', '1', '1', 'silver-run', '2025-11-01T00:00:00+00:00', '/synthetic/version', '/synthetic/publication.json', 'synthetic', 1, 0, 0, 1),
    ('0000320193', '0000320193-25-000002', 'filing.xml', 'checksum', 'COMPLETE', '1', '1', 'silver-run', '2025-11-01T00:00:00+00:00', '/synthetic/version', '/synthetic/publication.json', 'synthetic', 1, 0, 0, 1),
    ('0000320193', '0000320193-25-000003', 'filing.xml', 'checksum', 'COMPLETE', '1', '1', 'silver-run', '2025-11-01T00:00:00+00:00', '/synthetic/version', '/synthetic/publication.json', 'synthetic', 1, 0, 0, 1),
    ('0000320193', '0000320193-25-000004', 'filing.xml', 'checksum', 'COMPLETE', '1', '1', 'silver-run', '2025-11-01T00:00:00+00:00', '/synthetic/version', '/synthetic/publication.json', 'synthetic', 1, 0, 0, 1),
    ('0000320193', '0000320193-25-000005', 'filing.xml', 'checksum', 'COMPLETE', '1', '1', 'silver-run', '2025-11-01T00:00:00+00:00', '/synthetic/version', '/synthetic/publication.json', 'synthetic', 1, 0, 0, 1),
    ('0000320193', '0000320193-25-000009', 'filing.xml', 'checksum', 'COMPLETE', '1', '1', 'silver-run', '2025-11-01T00:00:00+00:00', '/synthetic/version', '/synthetic/publication.json', 'synthetic', 1, 0, 0, 1),
    ('0000789019', '0000320193-25-000001', 'filing.xml', 'checksum', 'COMPLETE', '1', '1', 'silver-run', '2025-11-01T00:00:00+00:00', '/synthetic/version', '/synthetic/publication.json', 'synthetic', 1, 0, 0, 1)
) as candidates(cik, accession_number, source_document_name, source_sha256, silver_status, parser_version, schema_version, activation_run_id, activated_at, version_path, publication_path, publication_sha256, accepted_count, dimension_count, rejected_count, candidate_count)
