{{ config(materialized='ephemeral') }}
-- Ephemeral (CTE nos modelos que dependem dele): nao cria view nem tabela. Uma view sobre
-- read_parquet grava o caminho absoluto do bronze no warehouse (quebra ao mudar de diretorio ou
-- SO) e uma tabela copiaria o texto dos posts de novo (+110 MiB). Assim o warehouse so guarda o
-- que os modelos derivados materializam e nenhum caminho.
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
