with expected as (
    select * from (values
        ('total_assets', 'http://fasb.org/us-gaap/', 'Assets', 1, 'instant'),
        ('total_liabilities', 'http://fasb.org/us-gaap/', 'Liabilities', 1, 'instant'),
        ('cash_and_equivalents', 'http://fasb.org/us-gaap/', 'CashAndCashEquivalentsAtCarryingValue', 1, 'instant')
    ) as mappings(metric_code, concept_namespace_prefix, concept_local_name, concept_priority, expected_period_kind)
), actual as (
    select * from {{ ref('metric_concept_map') }}
    where metric_code in ('total_assets', 'total_liabilities', 'cash_and_equivalents')
), missing as (
    select * from expected except select * from actual
), unexpected as (
    select * from actual except select * from expected
)
select * from missing
union all
select * from unexpected
