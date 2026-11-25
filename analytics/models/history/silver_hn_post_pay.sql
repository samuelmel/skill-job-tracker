-- Posts do HN "Who is hiring" que mencionam "$" nos dois anos mais recentes, com o cargo pelo trecho depois
-- do 1o "|" do cabecalho (posts sem "|" ficam fora), pela mesma seed role_families da coleta. Alimenta a base salarial da secao
-- Empregos (o texto fica aqui porque o parser de salario roda em Python). Sem familia ou na
-- familia "Outro": fora.
with posts as (
    select post_id, month, year, text, split_part(text, chr(10), 1) as header
    from {{ ref('silver_hn_post') }}
    where position('$' in text) > 0
      and position('|' in split_part(text, chr(10), 1)) > 0
      and year >= (select max(year) from {{ ref('silver_hn_post') }}) - 1
),
ranked as (
    select p.post_id, p.month, p.year, p.header, p.text, r.role
    from posts p
    join {{ ref('role_families') }} r
      on regexp_matches(strip_accents(lower({{ hn_role_part('p.header') }})), r.pattern)
    qualify row_number() over (partition by p.post_id order by r.ord) = 1
)
select * from ranked where role <> 'Outro'
