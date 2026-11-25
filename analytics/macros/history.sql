{# Bronze do historico do Hacker News (caminhos vem de VAYA_HN_POSTS / VAYA_HN_THREADS). #}
{% macro hn_posts_source() %}
read_parquet('{{ env_var("VAYA_HN_POSTS", "../data/history/hn/bronze/posts/*/*.parquet") }}', union_by_name = true)
{% endmacro %}

{% macro hn_threads_source() %}
read_parquet('{{ env_var("VAYA_HN_THREADS", "../data/history/hn/bronze/threads/*/*.parquet") }}', union_by_name = true)
{% endmacro %}

{# Trecho do cargo de um post do HN: o que vem depois do 1o "|" do cabecalho (1a linha). Sem "|" o
   cargo nao e extraivel com seguranca (o cabecalho inclui o nome da empresa). #}
{% macro hn_role_part(header) %}substr({{ header }}, position('|' in {{ header }}) + 1){% endmacro %}
