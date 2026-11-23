select * from read_parquet(
    '{{ env_var("VAYA_BRONZE_JOBS", "../data/bronze/jobs/*/*.parquet") }}',
    union_by_name = true
)
