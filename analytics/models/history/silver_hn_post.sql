{{ config(materialized='view') }}
-- View sobre o Parquet do bronze: o texto dos posts nao e copiado para o warehouse.
-- Posts de nivel superior das threads "Who is hiring" (HN), um por post_id. Sem autor. O raw ja
-- exclui deleted/dead/vazios; o filtro abaixo garante o mesmo aqui (denominador = posts validos).
select
    post_id,
    thread_id,
    month,
    cast(substr(month, 1, 4) as integer) as year,
    created_at,
    text,
    lower(text) as text_lower
from {{ hn_posts_source() }}
where text is not null and trim(text) <> ''
qualify row_number() over (partition by post_id order by thread_id) = 1
