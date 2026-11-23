-- Uma linha por vaga por mes (grao: source + external_id + mes): a ultima observacao do mes,
-- preferindo observacoes de run COMPLETA. Vaga vista so em run PARTIAL fica com
-- run_complete = false e nunca entra na tendencia.
select
    md5(source_key || '|' || external_id || '|' || fetched_month) as job_month_key,
    fetched_month,
    {{ is_data_family('job_family') }} as is_data_role,
    * exclude (fetched_month)
from {{ ref('silver_job_observation') }}
qualify row_number() over (
    partition by source_key, external_id, fetched_month
    order by run_complete desc, fetched_at desc
) = 1
