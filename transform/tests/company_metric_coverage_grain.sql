-- One row per company and metric.
select cik, metric_code, count(*) as row_count
from {{ ref('company_metric_coverage') }}
group by cik, metric_code
having count(*) <> 1
