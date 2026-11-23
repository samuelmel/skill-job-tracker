{# Remove HTML (inclusive o escapado como &lt;p&gt;) e normaliza espacos. #}
{% macro clean_html(col) %}
trim(regexp_replace(
    replace(replace(replace(replace(replace(replace(replace(
        regexp_replace(
            replace(replace({{ col }}, '&lt;', '<'), '&gt;', '>'),
            '<[^>]*>', ' ', 'g'),
        '&nbsp;', ' '), '&quot;', '"'), '&#39;', ''''), '&#x27;', ''''), '&rsquo;', ''''),
        '&amp;', '&'), '&ndash;', '-'),
    '\s+', ' ', 'g'))
{% endmacro %}
