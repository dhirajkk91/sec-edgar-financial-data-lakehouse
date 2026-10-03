with expected as (
    select * from (values
        ('operating_cash_flow', 'http://fasb.org/us-gaap/', 'NetCashProvidedByUsedInOperatingActivities', 1, 'duration'),
        ('capital_expenditures', 'http://fasb.org/us-gaap/', 'PaymentsToAcquirePropertyPlantAndEquipment', 1, 'duration')
    ) as mappings(metric_code, concept_namespace_prefix, concept_local_name, concept_priority, expected_period_kind)
), actual as (
    select * from {{ ref('metric_concept_map') }}
    where metric_code in ('operating_cash_flow', 'capital_expenditures')
), missing as (
    select * from expected except select * from actual
), unexpected as (
    select * from actual except select * from expected
)
select * from missing
union all
select * from unexpected
