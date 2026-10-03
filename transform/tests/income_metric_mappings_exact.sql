with expected as (
    select * from (values
        ('net_income', 'http://fasb.org/us-gaap/', 'NetIncomeLoss', 1, 'duration'),
        ('diluted_eps', 'http://fasb.org/us-gaap/', 'EarningsPerShareDiluted', 1, 'duration')
    ) as mappings(metric_code, concept_namespace_prefix, concept_local_name, concept_priority, expected_period_kind)
), actual as (
    select * from {{ ref('metric_concept_map') }}
    where metric_code in ('net_income', 'diluted_eps')
), missing as (
    select * from expected except select * from actual
), unexpected as (
    select * from actual except select * from expected
)
select * from missing
union all
select * from unexpected
