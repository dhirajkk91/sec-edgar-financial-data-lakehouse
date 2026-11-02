-- Where Gold withheld or flagged values, and why, across the whole universe.
SELECT metric_code,
       severity,
       issue_code,
       COUNT(*) AS issues,
       COUNT(DISTINCT cik) AS companies
FROM sec_gold.metric_quality_issues
GROUP BY metric_code, severity, issue_code
ORDER BY issues DESC;
