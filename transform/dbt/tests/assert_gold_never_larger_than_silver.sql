-- Gold only filters silver; it must never invent rows (e.g. through a bad join).
select gold_rows, silver_rows
from (
    select
        (select count(*) from {{ ref('trip_features') }}) as gold_rows,
        (select count(*) from {{ source('silver', 'yellow_trips') }}) as silver_rows
) counts
where gold_rows > silver_rows
