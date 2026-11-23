{#
  BI mart: demand and pace by month x weekday x hour (the "when is NYC slow?" view).
  Pace = total minutes / total miles, so long trips do not get lost in an average of
  ratios. Built from gold, so every BI number obeys the same quality rules as the model.
#}
select
    source_month,
    pickup_day_of_week,
    pickup_hour,
    count(*)                                         as trips,
    avg(duration_min)                                as avg_duration_min,
    avg(trip_distance)                               as avg_distance_mi,
    sum(duration_min) / nullif(sum(trip_distance), 0) as pace_min_per_mile
from {{ ref('trip_features') }}
group by source_month, pickup_day_of_week, pickup_hour
