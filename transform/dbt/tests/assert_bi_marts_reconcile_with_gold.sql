-- Every BI mart must account for exactly the gold rows: no trip lost, none double counted.
with gold as (select count(*) as n from {{ ref('trip_features') }}),
hourly as (select sum(trips) as n from {{ ref('bi_hourly_demand') }}),
daily as (select sum(trips) as n from {{ ref('bi_daily_trips') }}),
zones as (select sum(trips) as n from {{ ref('bi_pickup_zones') }})
select gold.n as gold_rows, hourly.n as hourly_rows, daily.n as daily_rows, zones.n as zone_rows
from gold, hourly, daily, zones
where hourly.n != gold.n or daily.n != gold.n or zones.n != gold.n
