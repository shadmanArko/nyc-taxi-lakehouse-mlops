-- BI mart: one row per pickup day (volume and typical ride over the month).
select
    source_month,
    pickup_date,
    max(pickup_day_of_week)    as pickup_day_of_week,
    max(cast(is_weekend as int)) = 1 as is_weekend,
    count(*)                   as trips,
    avg(duration_min)          as avg_duration_min,
    avg(trip_distance)         as avg_distance_mi
from {{ ref('trip_features') }}
group by source_month, pickup_date
