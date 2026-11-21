{#
  ISO day of week: 1 = Monday ... 7 = Sunday, on every engine.
  Spark's dayofweek() starts at Sunday = 1 and DuckDB's at Sunday = 0, so a naive
  call would silently shift every weekday feature between CI and production.
#}
{% macro iso_day_of_week(ts) -%}
    {{ return(adapter.dispatch('iso_day_of_week', 'taxi')(ts)) }}
{%- endmacro %}

{% macro databricks__iso_day_of_week(ts) -%}
    extract(DAYOFWEEK_ISO from {{ ts }})
{%- endmacro %}

{% macro duckdb__iso_day_of_week(ts) -%}
    isodow({{ ts }})
{%- endmacro %}
