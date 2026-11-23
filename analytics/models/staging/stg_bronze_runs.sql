select * from read_parquet(
    '{{ env_var("VAYA_BRONZE_RUNS", "../data/bronze/runs/*/*.parquet") }}',
    union_by_name = true
)
