-- Tendencia mes a mes sobre um PAINEL COMPARAVEL: so fontes com run completa nos DOIS meses
-- (mes e mes anterior com dados). Fonte nova ou run PARTIAL nunca entra, para nao parecer
-- crescimento do mercado. Primeiro mes: deltas NULL e status 'baseline'.
with months as (
    select
        fetched_month as month,
        lag(fetched_month) over (order by fetched_month) as prev_month
    from (
        select distinct fetched_month from {{ ref('silver_source_month') }} where has_complete_run
    )
),
comp as (
    select source_key, fetched_month
    from {{ ref('silver_source_month') }}
    where has_complete_run
),
panel as (
    select m.month, m.prev_month, c.source_key
    from months m
    join comp c on c.fetched_month = m.month
    where m.prev_month is null
       or exists (
            select 1 from comp p
            where p.source_key = c.source_key and p.fetched_month = m.prev_month
       )
),
jobs as (
    select job_month_key, fetched_month, source_key, is_data_role, has_description
    from {{ ref('silver_job_month') }}
    where run_complete
),
pair_jobs as (
    select p.month, 'cur' as side, j.*
    from panel p
    join jobs j on j.source_key = p.source_key and j.fetched_month = p.month
    union all
    select p.month, 'prev' as side, j.*
    from panel p
    join jobs j on j.source_key = p.source_key and j.fetched_month = p.prev_month
    where p.prev_month is not null
),
scoped as (
    select month, side, 'all' as scope, job_month_key, source_key, has_description from pair_jobs
    union all
    select month, side, 'data_roles' as scope, job_month_key, source_key, has_description
    from pair_jobs where is_data_role
),
denoms as (
    select month, side, scope,
           count(*) filter (where has_description) as denominator
    from scoped
    group by month, side, scope
),
counts as (
    select s.month, s.side, s.scope, k.skill, count(*) as n
    from scoped s
    join {{ ref('silver_job_skill') }} k on k.job_month_key = s.job_month_key
    group by s.month, s.side, s.scope, k.skill
),
panel_size as (
    select month, count(*) as panel_sources from panel group by month
),
grid as (
    select m.month, m.prev_month, sc.scope, sk.skill, sk.category
    from months m
    cross join (select 'all' as scope union all select 'data_roles') sc
    cross join {{ ref('skills') }} sk
),
sides as (
    select
        g.month, g.prev_month, g.scope, g.skill, g.category,
        coalesce(ps.panel_sources, 0) as panel_sources,
        coalesce(dc.denominator, 0) as denominator,
        coalesce(cc.n, 0) as n,
        coalesce(dp.denominator, 0) as prev_denominator,
        coalesce(cp.n, 0) as prev_n
    from grid g
    left join panel_size ps on ps.month = g.month
    left join denoms dc on dc.month = g.month and dc.side = 'cur' and dc.scope = g.scope
    left join denoms dp on dp.month = g.month and dp.side = 'prev' and dp.scope = g.scope
    left join counts cc
        on cc.month = g.month and cc.side = 'cur' and cc.scope = g.scope and cc.skill = g.skill
    left join counts cp
        on cp.month = g.month and cp.side = 'prev' and cp.scope = g.scope and cp.skill = g.skill
),
ranked as (
    select *,
        case when n > 0 then rank() over (partition by month, scope order by n desc) end as rank,
        case when prev_n > 0
            then rank() over (partition by month, scope order by prev_n desc) end as prev_rank,
        case when denominator > 0 then 100.0 * n / denominator end as share_raw,
        case when prev_denominator > 0 then 100.0 * prev_n / prev_denominator end as prev_share_raw
    from sides
),
stats as (
    select *,
        case when prev_month is not null and denominator > 0 and prev_denominator > 0
            then share_raw - prev_share_raw end as delta_raw,
        case when prev_month is not null and denominator > 0 and prev_denominator > 0
            then (n + prev_n) * 1.0 / (denominator + prev_denominator) end as pooled
    from ranked
),
final as (
    select *,
        case when pooled > 0 and pooled < 1 then
            ((n * 1.0 / denominator) - (prev_n * 1.0 / prev_denominator))
            / sqrt(pooled * (1 - pooled) * (1.0 / denominator + 1.0 / prev_denominator))
        end as z
    from stats
)
select
    month, prev_month, scope, skill, category, panel_sources,
    n, denominator, round(share_raw, 2) as share_pct,
    prev_n, prev_denominator, round(prev_share_raw, 2) as prev_share_pct,
    round(delta_raw, 2) as delta_pp,
    rank, prev_rank,
    case when rank is not null and prev_rank is not null then prev_rank - rank end as rank_change,
    round(z, 2) as z,
    (denominator < {{ var('min_sample') }}
        or (prev_month is not null and prev_denominator < {{ var('min_sample') }})) as low_sample,
    case
        when prev_month is null then 'baseline'
        when panel_sources = 0 or denominator = 0 or prev_denominator = 0 then 'no_panel'
        when denominator < {{ var('min_sample') }}
          or prev_denominator < {{ var('min_sample') }} then 'low_sample'
        when n = 0 and prev_n = 0 then 'absent'
        when z >= {{ var('z_threshold') }} and delta_raw >= {{ var('min_delta_pp') }} then 'rising'
        when z <= -{{ var('z_threshold') }} and delta_raw <= -{{ var('min_delta_pp') }} then 'falling'
        else 'stable'
    end as status
from final
