-- Familia de cargo (secao Empregos) por vaga-mes: a primeira regra do seed role_families (por
-- `ord`) que casa com o titulo normalizado vence. Regras "Outro" vem antes das familias e
-- excluem cargos nao tecnicos ("Sales Engineer"); vaga sem nenhuma regra nao entra na tabela.
select job_month_key, role
from (
    select m.job_month_key, r.role, r.ord
    from {{ ref('silver_job_month') }} m
    join {{ ref('role_families') }} r on regexp_matches(m.title_norm, r.pattern)
)
qualify row_number() over (partition by job_month_key order by ord) = 1
