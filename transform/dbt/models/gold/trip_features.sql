{{ config(contract={"enforced": true}) }}

{#
  Gold: one row per cleaned trip, with only what is known at pickup time plus the
  target. Training (Phase 4) reads this table; its columns and types are the
  contract in libs/contracts (GOLD_TRIP_FEATURES), enforced here.
#}

with silver as (
    select * from {{ source('silver', 'yellow_trips') }}
),

known_zones as (
    -- EDA s6, rule R7: zones 264/265 are "Unknown"/"Outside NYC" and carry no location signal.
    select * from silver
    where pickup_location_id not in (264, 265)
      and dropoff_location_id not in (264, 265)
)

select
    cast({{ dbt_utils.generate_surrogate_key([
        'vendor_id', 'pickup_datetime', 'dropoff_datetime',
        'pickup_location_id', 'dropoff_location_id', 'trip_distance',
    ]) }} as string)                                              as trip_id,
    cast(source_month as date)                                    as source_month,
    cast(pickup_datetime as timestamp)                            as pickup_datetime,
    cast(pickup_datetime as date)                                 as pickup_date,
    cast(extract(hour from pickup_datetime) as int)               as pickup_hour,
    cast({{ iso_day_of_week('pickup_datetime') }} as int)         as pickup_day_of_week,
    cast({{ iso_day_of_week('pickup_datetime') }} >= 6 as boolean) as is_weekend,
    cast(pickup_location_id as int)                               as pickup_location_id,
    cast(dropoff_location_id as int)                              as dropoff_location_id,
    cast(trip_distance as double)                                 as trip_distance,
    cast(is_metadata_missing as boolean)                          as is_metadata_missing,
    cast(duration_min as double)                                  as duration_min
from known_zones
