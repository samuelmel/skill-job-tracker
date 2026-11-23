-- Participacao de cada skill por mes e escopo, sobre fontes com run COMPLETA no mes.
-- Denominador = so vagas COM descricao. Descritivo: a composicao de fontes pode variar de um
-- mes para outro; comparacao entre meses so vale em gold_skill_trend (painel comparavel).
with jobs as (
    select job_month_key, fetched_month as month, source_key, is_data_role, has_description
    from {{ ref('silver_job_month') }}
    where run_complete
),
scoped as (
    select month, 'all' as scope, job_month_key, source_key, has_description from jobs
    union all
    select month, 'data_roles' as scope, job_month_key, source_key, has_description
    from jobs where is_data_role
),
denoms as (
    select
        month, scope,
        count(*) as n_jobs,
        count(*) filter (where has_description) as denominator,
        count(distinct source_key) as n_sources
    from scoped
    group by month, scope
),
counts as (
    select s.month, s.scope, k.skill, count(*) as n
    from scoped s
    join {{ ref('silver_job_skill') }} k on k.job_month_key = s.job_month_key
    group by s.month, s.scope, k.skill
),
grid as (
    select d.month, d.scope, d.n_jobs, d.denominator, d.n_sources, sk.skill, sk.category
    from denoms d
    cross join {{ ref('skills') }} sk
),
joined as (
    select
        g.month, g.scope, g.skill, g.category,
        coalesce(c.n, 0) as n,
        g.denominator,
        g.n_jobs,
        g.n_sources
    from grid g
    left join counts c
        on c.month = g.month and c.scope = g.scope and c.skill = g.skill
)
select
    month, scope, skill, category, n, denominator, n_jobs, n_sources,
    case when denominator > 0 then round(100.0 * n / denominator, 2) end as share_pct,
    case when n > 0 then rank() over (partition by month, scope order by n desc) end as rank,
    denominator < {{ var('min_sample') }} as low_sample
from joined
