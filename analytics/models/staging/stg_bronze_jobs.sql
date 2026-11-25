select * from read_parquet(
    '{{ env_var("SKILOMETER_BRONZE_JOBS", "../data/bronze/jobs/*/*.parquet") }}',
    union_by_name = true
)
{%- set failed = env_var('SKILOMETER_FAILED_MONTHS', '').split(',') | select | list %}
{% if failed | length > 0 -%}
where fetched_month not in ({% for m in failed %}'{{ m }}'{{ ', ' if not loop.last }}{% endfor %})
{%- endif %}
