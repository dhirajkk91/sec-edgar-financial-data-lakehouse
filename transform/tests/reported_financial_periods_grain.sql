select cik, accession_number, metric_code, period_kind, period_start, period_end, period_instant
from {{ ref('int_reported_financial_periods') }}
group by cik, accession_number, metric_code, period_kind, period_start, period_end, period_instant
having count(*) > 1
