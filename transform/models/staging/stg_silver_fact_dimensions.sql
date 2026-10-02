select
    source_occurrence_id,
    axis_namespace,
    axis_name,
    member_kind,
    member_value,
    typed_member_xml,
    context_location
from {{ source('silver', 'fact_dimensions') }}
