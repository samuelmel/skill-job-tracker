-- Familia de cargo de cada post do HN "Who is hiring", pela seed role_families aplicada ao trecho
-- do cargo: o que vem depois do 1o "|" da 1a linha ("Empresa | Cargo | Local ..."). Sem "|" no
-- cabecalho o cargo nao e extraivel com seguranca: o post fica sem linha aqui (fora do
-- denominador). A 1a regra por ord vence; se for "Outro" (cargo nao tecnico/lideranca) o post sai.
with posts as (
    select post_id, month, year,
           {{ hn_role_part("split_part(text, chr(10), 1)") }} as role_part
    from {{ ref('silver_hn_post') }}
    where position('|' in split_part(text, chr(10), 1)) > 0
),
ranked as (
    select p.post_id, p.month, p.year, r.role
    from posts p
    join {{ ref('role_families') }} r
      on regexp_matches(strip_accents(lower(p.role_part)), r.pattern)
    qualify row_number() over (partition by p.post_id order by r.ord) = 1
)
select post_id, month, year, role from ranked where role <> 'Outro'
