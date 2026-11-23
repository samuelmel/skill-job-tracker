-- Cobertura por mes do historico do HN: threads, posts validos, exclusoes e posts com skill.
with threads as (
    select
        month,
        count(*) as n_threads,
        sum(api_top_level_count) as api_top_level_count,
        sum(firebase_kids_count) as firebase_kids_count,
        sum(excluded_deleted_or_empty) as excluded_deleted_or_empty,
        bool_and(complete) as complete
    from {{ hn_threads_source() }}
    group by month
),
posts as (
    select month, year, count(*) as n_posts
    from {{ ref('silver_hn_post') }}
    group by month, year
),
with_skill as (
    select month, count(distinct post_id) as n_posts_with_skill
    from {{ ref('silver_hn_post_skill') }}
    group by month
)
select
    p.month, p.year,
    coalesce(t.n_threads, 0) as n_threads,
    p.n_posts,
    coalesce(t.api_top_level_count, 0) as api_top_level_count,
    coalesce(t.firebase_kids_count, 0) as firebase_kids_count,
    coalesce(t.excluded_deleted_or_empty, 0) as excluded_deleted_or_empty,
    coalesce(w.n_posts_with_skill, 0) as n_posts_with_skill,
    round(100.0 * coalesce(w.n_posts_with_skill, 0) / p.n_posts, 1) as pct_with_skill,
    coalesce(t.complete, false) as complete,
    p.n_posts < {{ var('min_sample') }} as low_sample
from posts p
left join threads t on t.month = p.month
left join with_skill w on w.month = p.month
