select source_occurrence_id, dimension_count, has_dimensions
from {{ ref('int_financial_metric_candidates') }}
where dimension_count is null
    or dimension_count < 0
    or has_dimensions is distinct from (dimension_count > 0)
