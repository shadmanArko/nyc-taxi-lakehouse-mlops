-- BI mart: activity per pickup zone and month (where rides start, and how long they take).
select
    source_month,
    pickup_location_id,
    pickup_location_id in (1, 132, 138)   as is_airport,   -- EWR, JFK, LaGuardia
    count(*)                              as trips,
    avg(duration_min)                     as avg_duration_min,
    avg(trip_distance)                    as avg_distance_mi
from {{ ref('trip_features') }}
group by source_month, pickup_location_id
