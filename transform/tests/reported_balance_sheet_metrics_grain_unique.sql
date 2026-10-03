select cik, accession_number, metric_code, period_instant
from {{ ref('int_reported_balance_sheet_metrics') }}
group by cik, accession_number, metric_code, period_instant
having count(*) > 1
