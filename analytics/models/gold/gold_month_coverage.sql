-- Cobertura por mes: quantas fontes/runs, quantas vagas e quanto do painel e comparavel.
with runs as (
    select
        fetched_month as month,
        count(*) as n_runs,
        count(*) filter (where complete) as n_runs_complete,
        count(*) filter (where not complete) as n_runs_partial,
        count(distinct source_key) as n_sources_seen,
        count(distinct source_key) filter (where complete) as n_sources_complete,
        count(distinct platform) as n_platforms
    from {{ ref('silver_run') }}
    group by fetched_month
),
jobs as (
    select
        fetched_month as month,
        count(*) filter (where run_complete) as n_jobs,
        count(*) filter (where run_complete and has_description) as n_jobs_with_description,
        count(*) filter (where run_complete and is_data_role) as n_data_role_jobs,
        count(*) filter (where run_complete and is_data_role and has_description)
            as n_data_role_with_description,
        count(*) filter (where not run_complete) as n_jobs_partial_only
    from {{ ref('silver_job_month') }}
    group by fetched_month
),
months as (
    select
        fetched_month as month,
        lag(fetched_month) over (order by fetched_month) as prev_month
    from (
        select distinct fetched_month from {{ ref('silver_source_month') }} where has_complete_run
    )
),
panel as (
    select m.month, count(*) as panel_sources
    from months m
    join {{ ref('silver_source_month') }} c on c.fetched_month = m.month and c.has_complete_run
    where m.prev_month is not null
      and exists (
        select 1 from {{ ref('silver_source_month') }} p
        where p.source_key = c.source_key and p.fetched_month = m.prev_month and p.has_complete_run
      )
    group by m.month
)
select
    r.month,
    mo.prev_month,
    r.n_runs, r.n_runs_complete, r.n_runs_partial,
    r.n_sources_seen, r.n_sources_complete, r.n_platforms,
    coalesce(j.n_jobs, 0) as n_jobs,
    coalesce(j.n_jobs_with_description, 0) as n_jobs_with_description,
    case when coalesce(j.n_jobs, 0) > 0
        then round(100.0 * j.n_jobs_with_description / j.n_jobs, 1) end as pct_with_description,
    coalesce(j.n_data_role_jobs, 0) as n_data_role_jobs,
    coalesce(j.n_data_role_with_description, 0) as n_data_role_with_description,
    coalesce(j.n_jobs_partial_only, 0) as n_jobs_partial_only,
    coalesce(p.panel_sources, 0) as panel_sources,
    coalesce(mo.month is not null and mo.prev_month is null, false) as is_baseline,
    coalesce(p.panel_sources, 0) > 0 as trend_available
from runs r
left join jobs j on j.month = r.month
left join months mo on mo.month = r.month
left join panel p on p.month = r.month
