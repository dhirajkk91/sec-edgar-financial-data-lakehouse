select
    metric_code,
    concept_namespace_prefix,
    concept_local_name,
    expected_period_kind
from {{ ref('metric_concept_map') }}
group by
    metric_code,
    concept_namespace_prefix,
    concept_local_name,
    expected_period_kind
having count(*) > 1
