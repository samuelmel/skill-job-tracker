-- Por fonte e mes: houve ao menos uma run completa? So essas fontes entram no painel.
select
    source_key,
    any_value(platform) as platform,
    fetched_month,
    count(*) as n_runs,
    count(*) filter (where complete) as n_runs_complete,
    count(*) filter (where not complete) as n_runs_partial,
    count(*) filter (where complete) > 0 as has_complete_run,
    max(fetched_at) as last_fetched_at
from {{ ref('silver_run') }}
group by source_key, fetched_month
