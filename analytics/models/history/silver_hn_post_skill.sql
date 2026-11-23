-- depends_on: {{ ref('skills') }}
{#- Mesmos padroes de seeds/skills.csv do fluxo mensal (case_sensitive e exclude_pattern
    incluidos), aplicados ao texto do post. Um SELECT por skill com o regex como constante.
    Limite de palavra: o vizinho nao pode ser letra, digito, "_", "+" ou "#". -#}
{% set skill_rows = [] %}
{% if execute %}
    {% set res = run_query('select skill, category, pattern, case_sensitive, coalesce(exclude_pattern, \'\') as exclude_pattern from ' ~ ref('skills') ~ ' order by skill') %}
    {% set skill_rows = res.rows %}
{% endif %}

with posts as (
    select post_id, month, year, text as text_orig, text_lower from {{ ref('silver_hn_post') }}
),
matched as (
{% if skill_rows | length == 0 %}
select post_id, month, year, cast(null as varchar) as skill, cast(null as varchar) as category
from posts where false
{% else %}
{% for r in skill_rows %}
{% set col = 'text_orig' if r[3] else 'text_lower' %}
{% set cls = 'A-Za-z0-9_+#' if r[3] else 'a-z0-9_+#' %}
{% if not loop.first %}union all{% endif %}
select post_id, month, year,
       '{{ r[0] | replace("'", "''") }}' as skill, '{{ r[1] | replace("'", "''") }}' as category
from posts
where regexp_matches({{ col }}, '(?:^|[^{{ cls }}])(?:{{ r[2] | replace("'", "''") }})(?:$|[^{{ cls }}])')
{% if r[4] %}  and not regexp_matches({{ col }}, '{{ r[4] | replace("'", "''") }}'){% endif %}

{% endfor %}
{% endif %}
)
select md5(cast(post_id as varchar) || '|' || skill) as post_skill_key, *
from matched
