-- depends_on: {{ ref('skills') }}
{#- Gera um SELECT por skill do seed com o regex como constante (RE2 compila uma vez por
    consulta, em vez de uma vez por linha). Limite de palavra: o caractere vizinho nao pode
    ser letra, digito, "_", "+" ou "#" (assim "c" nao casa em "c++", "java" nao casa em
    "javascript"). Skills de texto sensivel a maiusculas usam o texto original. -#}
{% set skill_rows = [] %}
{% if execute %}
    {% set res = run_query('select skill, category, pattern, case_sensitive, coalesce(exclude_pattern, \'\') as exclude_pattern from ' ~ ref('skills') ~ ' order by skill') %}
    {% set skill_rows = res.rows %}
{% endif %}

with jobs as (
    select
        job_month_key,
        fetched_month,
        source_key,
        run_complete,
        is_data_role,
        title || ' . ' || description_clean as text_orig,
        lower(title || ' . ' || description_clean) as text_lower
    from {{ ref('silver_job_month') }}
    where has_description
)
{% if skill_rows | length == 0 %}
select job_month_key, fetched_month, source_key, run_complete, is_data_role,
       cast(null as varchar) as skill, cast(null as varchar) as category
from jobs where false
{% else %}
{% for r in skill_rows %}
{% set col = 'text_orig' if r[3] else 'text_lower' %}
{% set cls = 'A-Za-z0-9_+#' if r[3] else 'a-z0-9_+#' %}
{% if not loop.first %}union all{% endif %}
select job_month_key, fetched_month, source_key, run_complete, is_data_role,
       '{{ r[0] | replace("'", "''") }}' as skill, '{{ r[1] | replace("'", "''") }}' as category
from jobs
where regexp_matches({{ col }}, '(?:^|[^{{ cls }}])(?:{{ r[2] | replace("'", "''") }})(?:$|[^{{ cls }}])')
{% if r[4] %}  and not regexp_matches({{ col }}, '{{ r[4] | replace("'", "''") }}'){% endif %}

{% endfor %}
{% endif %}
