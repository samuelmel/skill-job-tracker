-- Participacao de cada skill por mes no historico do HN. Denominador = posts de nivel superior
-- validos do mes (sem deleted/dead/vazios). Serie SEPARADA da coleta atual: nao emendar.
with denoms as (
    select month, year, count(*) as denominator
    from {{ ref('silver_hn_post') }}
    group by month, year
),
counts as (
    select month, skill, count(*) as n
    from {{ ref('silver_hn_post_skill') }}
    group by month, skill
),
grid as (
    select d.month, d.year, d.denominator, sk.skill, sk.category
    from denoms d
    cross join {{ ref('skills') }} sk
),
joined as (
    select g.month, g.year, g.skill, g.category, coalesce(c.n, 0) as n, g.denominator
    from grid g
    left join counts c on c.month = g.month and c.skill = g.skill
)
select
    month, year, skill, category, n, denominator,
    round(100.0 * n / denominator, 2) as share_pct,
    case when n > 0 then rank() over (partition by month order by n desc) end as rank,
    denominator < {{ var('min_sample') }} as low_sample
from joined
