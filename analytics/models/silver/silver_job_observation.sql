-- Observacao limpa de uma vaga numa run (grao: source + external_id + run).
-- Linhas com normalize_error ficam no bronze, mas nao entram na analise.
with base as (
    select
        platform,
        source_external_id,
        platform || '/' || source_external_id as source_key,
        run_id,
        cast(complete as boolean) as run_complete,
        cast(fetched_at as timestamp) as fetched_at,
        external_id,
        trim(regexp_replace(title, '\s+', ' ', 'g')) as title,
        company_name,
        {{ clean_html('description') }} as description_clean,
        location,
        original_url,
        application_url,
        employment_type,
        workplace_type,
        salary_min,
        salary_max,
        salary_currency,
        salary_period,
        original_posted_at
    from {{ ref('stg_bronze_jobs') }}
    where normalize_error is null
      and title is not null
      and trim(title) <> ''
      and external_id is not null
      and external_id <> ''
),
dedup as (
    select *, row_number() over (
        partition by source_key, external_id, run_id order by fetched_at desc
    ) as rn
    from base
),
typed as (
    select
        md5(source_key || '|' || external_id || '|' || run_id) as observation_key,
        * exclude (rn),
        strftime(fetched_at, '%Y-%m') as fetched_month,
        strip_accents(lower(title)) as title_norm,
        coalesce(length(description_clean), 0) >= 20 as has_description
    from dedup
    where rn = 1
)
select
    *,
    {{ job_family('title_norm') }} as job_family,
    {{ seniority('title_norm') }} as seniority
from typed
