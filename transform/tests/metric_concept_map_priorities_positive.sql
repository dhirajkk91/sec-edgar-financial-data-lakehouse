select metric_code, concept_local_name, concept_priority
from {{ ref('metric_concept_map') }}
where concept_priority is null or concept_priority <= 0
