select cik, accession_number, metric_code, period_start, period_end
from {{ ref('int_reported_cash_flow_metrics') }}
group by cik, accession_number, metric_code, period_start, period_end
having count(*) > 1
