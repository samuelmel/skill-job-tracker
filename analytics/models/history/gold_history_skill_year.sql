-- Rollup anual do historico do HN. Ano parcial (menos de 12 meses com dados: 2011 comeca em
-- abril, o ano corrente ainda nao fechou) e comparado pela MEDIA DAS PARTICIPACOES MENSAIS dos
-- meses disponiveis, nao pela soma: assim 9 ou 11 meses valem o mesmo que 12.
-- delta_pp compara com o ano anterior (media mensal) e so existe quando ha ano anterior.
with yearly as (
    select
        year, skill, category,
        sum(n) as n,
        sum(denominator) as denominator,
        count(*) as months_covered,
        avg(100.0 * n / denominator) as mean_month_share
    from {{ ref('gold_history_skill_month') }}
    group by year, skill, category
),
ranked as (
    select *,
        100.0 * n / denominator as pooled_share,
        case when n > 0
            then rank() over (partition by year order by mean_month_share desc) end as rank,
        lag(mean_month_share) over (partition by skill order by year) as prev_share_raw,
        lag(n) over (partition by skill order by year) as prev_n,
        lag(year) over (partition by skill order by year) as prev_year
    from yearly
)
select
    year, skill, category, n, denominator, months_covered,
    months_covered < 12 as partial_year,
    round(pooled_share, 2) as pooled_share_pct,
    round(mean_month_share, 2) as mean_month_share_pct,
    rank,
    prev_year,
    prev_n,
    round(prev_share_raw, 2) as prev_mean_month_share_pct,
    case when prev_year = year - 1 then round(mean_month_share - prev_share_raw, 2) end as delta_pp,
    denominator < {{ var('min_sample') }} as low_sample,
    -- ruido: skill com poucas mencoes nos dois anos nao entra em ranking de altas/quedas
    (n >= {{ var('min_sample') }} or coalesce(prev_n, 0) >= {{ var('min_sample') }}) as has_signal
from ranked
