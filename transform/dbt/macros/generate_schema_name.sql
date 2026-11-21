{#
  Use the configured schema as-is (taxi_gold), instead of dbt's default
  "<target_schema>_<custom_schema>". The medallion schema names are part of the
  data contract, so they must not depend on who runs dbt.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none or target.type == 'duckdb' -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
