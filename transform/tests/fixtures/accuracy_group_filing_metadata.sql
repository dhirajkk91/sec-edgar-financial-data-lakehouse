-- Synthetic filing identity for accuracy-group regressions.
select
    cik::VARCHAR as cik,
    accession_number::VARCHAR as accession_number,
    form::VARCHAR as form,
    filing_date::DATE as filing_date,
    report_date::DATE as report_date,
    primary_document::VARCHAR as primary_document,
    metadata_run_id::VARCHAR as metadata_run_id,
    run_record_path::VARCHAR as run_record_path,
    submissions_path::VARCHAR as submissions_path,
    submissions_url::VARCHAR as submissions_url,
    submissions_retrieved_at::TIMESTAMP WITH TIME ZONE as submissions_retrieved_at,
    submissions_size_bytes::BIGINT as submissions_size_bytes,
    submissions_sha256::VARCHAR as submissions_sha256
from (values
    ('0000320193', '0000320193-25-000001', '10-Q', '2025-11-01', '2025-09-30', 'filing.htm', 'metadata-run', '/synthetic/run.json', '/synthetic/submissions.json', 'https://example.invalid/submissions', '2025-11-01T00:00:00+00:00', 1, 'snapshot-checksum'),
    ('0000320193', '0000320193-25-000002', '10-Q', '2025-11-01', '2025-09-30', 'filing.htm', 'metadata-run', '/synthetic/run.json', '/synthetic/submissions.json', 'https://example.invalid/submissions', '2025-11-01T00:00:00+00:00', 1, 'snapshot-checksum'),
    ('0000320193', '0000320193-25-000003', '10-Q', '2025-11-01', '2025-09-30', 'filing.htm', 'metadata-run', '/synthetic/run.json', '/synthetic/submissions.json', 'https://example.invalid/submissions', '2025-11-01T00:00:00+00:00', 1, 'snapshot-checksum'),
    ('0000320193', '0000320193-25-000004', '10-Q', '2025-11-01', '2025-09-30', 'filing.htm', 'metadata-run', '/synthetic/run.json', '/synthetic/submissions.json', 'https://example.invalid/submissions', '2025-11-01T00:00:00+00:00', 1, 'snapshot-checksum'),
    ('0000320193', '0000320193-25-000005', '10-Q', '2025-11-01', '2025-09-30', 'filing.htm', 'metadata-run', '/synthetic/run.json', '/synthetic/submissions.json', 'https://example.invalid/submissions', '2025-11-01T00:00:00+00:00', 1, 'snapshot-checksum'),
    ('0000320193', '0000320193-25-000009', '10-Q', '2025-11-01', '2025-09-30', 'filing.htm', 'metadata-run', '/synthetic/run.json', '/synthetic/submissions.json', 'https://example.invalid/submissions', '2025-11-01T00:00:00+00:00', 1, 'snapshot-checksum'),
    ('0000789019', '0000320193-25-000001', '10-Q', '2025-11-01', '2025-09-30', 'filing.htm', 'metadata-run', '/synthetic/run.json', '/synthetic/submissions.json', 'https://example.invalid/submissions', '2025-11-01T00:00:00+00:00', 1, 'snapshot-checksum')
) as candidates(cik, accession_number, form, filing_date, report_date, primary_document, metadata_run_id, run_record_path, submissions_path, submissions_url, submissions_retrieved_at, submissions_size_bytes, submissions_sha256)
