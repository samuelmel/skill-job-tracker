-- Saude por fonte: runs completas vs PARTIAL, vagas na ultima run, cobertura de descricao
-- e taxa de falha de normalizacao.
with runs as (
    select
        source_key,
        any_value(platform) as platform,
        count(*) as n_runs,
        count(*) filter (where complete) as n_runs_complete,
        count(*) filter (where not complete) as n_runs_partial,
        max(fetched_at) as last_fetched_at,
        sum(rows_bronze) as rows_total,
        sum(normalize_errors) as normalize_errors_total,
        count(distinct fetched_month) filter (where complete) as months_with_complete
    from {{ ref('silver_run') }}
    group by source_key
),
last_run as (
    select source_key, complete as last_run_complete, job_count as last_run_jobs
    from {{ ref('silver_run') }}
    qualify row_number() over (partition by source_key order by fetched_at desc) = 1
),
jobs as (
    select
        source_key,
        count(*) as n_job_months,
        count(*) filter (where has_description) as n_with_description
    from {{ ref('silver_job_month') }}
    where run_complete
    group by source_key
)
select
    r.source_key, r.platform, r.n_runs, r.n_runs_complete, r.n_runs_partial,
    r.last_fetched_at, l.last_run_complete, l.last_run_jobs,
    r.months_with_complete,
    case when r.rows_total > 0
        then round(100.0 * r.normalize_errors_total / r.rows_total, 1) end as normalize_error_pct,
    case when coalesce(j.n_job_months, 0) > 0
        then round(100.0 * j.n_with_description / j.n_job_months, 1) end as pct_with_description,
    case
        when r.n_runs_complete = 0 then 'sem_run_completa'
        when not l.last_run_complete then 'ultima_run_parcial'
        when coalesce(l.last_run_jobs, 0) = 0 then 'vazia'
        when r.rows_total > 0 and r.normalize_errors_total * 1.0 / r.rows_total > 0.2
            then 'erros_normalizacao'
        else 'ok'
    end as health
from runs r
join last_run l using (source_key)
left join jobs j using (source_key)
