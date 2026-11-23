-- Uma linha por run do raw (grao: run_id). Runs sem vagas tambem entram: uma fonte
-- completa com 0 vagas continua sendo uma observacao valida.
select
    run_id,
    platform,
    source_external_id,
    platform || '/' || source_external_id as source_key,
    cast(fetched_at as timestamp) as fetched_at,
    strftime(cast(fetched_at as timestamp), '%Y-%m') as fetched_month,
    cast(complete as boolean) as complete,
    cast(job_count as bigint) as job_count,
    cast(rows_bronze as bigint) as rows_bronze,
    cast(normalize_errors as bigint) as normalize_errors,
    connector
from (
    select *, row_number() over (partition by run_id order by fetched_at desc) as rn
    from {{ ref('stg_bronze_runs') }}
)
where rn = 1
