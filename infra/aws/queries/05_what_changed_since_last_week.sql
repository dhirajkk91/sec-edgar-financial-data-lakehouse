-- Iceberg time travel: filings in Gold now that weren't there seven days ago.
-- Every weeknight publish is a snapshot, so the lake answers "what's new?" itself.
SELECT f.cik, f.accession_number, f.form, f.filing_date
FROM sec_gold.dim_filings AS f
WHERE NOT EXISTS (
    SELECT 1
    FROM sec_gold.dim_filings FOR TIMESTAMP AS OF (current_timestamp - INTERVAL '7' DAY) AS old
    WHERE old.accession_number = f.accession_number
)
ORDER BY f.filing_date DESC;
