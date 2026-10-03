select cik, accession_number, metric_code, period_start, period_end
from {{ ref('int_reported_income_metrics') }}
group by cik, accession_number, metric_code, period_start, period_end
having count(*) > 1
