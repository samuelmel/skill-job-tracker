{# Familia de cargo a partir do titulo (minusculo, sem acento). Limite de palavra em todo padrao:
   "etl" nao pode casar com "retail". A primeira familia que casar vence. #}
{% macro job_family(t) %}
case
    when regexp_matches({{ t }}, '\b(data engineer\w*|engenheir[oa] de dados|etl|big data (engineer|developer)|data platform engineer|dataops|data pipeline engineer)\b') then 'data_engineer'
    when regexp_matches({{ t }}, '\banalytics engineer\w*\b') then 'analytics_engineer'
    when regexp_matches({{ t }}, '\b(data scientist\w*|cientista de dados|applied scientist|research scientist|decision scientist)\b') then 'data_scientist'
    when regexp_matches({{ t }}, '\b((machine learning|ml|mlops|ai|llm|deep learning) (engineer|developer|scientist)|engenheir[oa] de (machine learning|ml|ia))\b') then 'ml_engineer'
    when regexp_matches({{ t }}, '\b(data analyst\w*|analista de dados|bi analyst|bi developer|business intelligence|reporting analyst|analytics analyst|insights analyst|analytics (manager|lead|specialist))\b') then 'data_analyst'
    when regexp_matches({{ t }}, '\b(data architect\w*|arquitet[oa] de dados|database administrator|dba|data modeler|data steward)\b') then 'data_architect'
    when regexp_matches({{ t }}, '\b(devops|sre|site reliability|platform engineer|cloud engineer|infrastructure engineer|systems? administrator)\b') then 'devops_infra'
    when regexp_matches({{ t }}, '\b(security|cybersecurity|infosec|soc analyst|penetration)\b') then 'security'
    when regexp_matches({{ t }}, '\b(software (engineer|developer)|developer|desenvolvedor\w*|programmer|full ?stack|back ?end|front ?end|swe|sde|mobile engineer|web engineer|engineering manager)\b') then 'software_engineer'
    when regexp_matches({{ t }}, '\b(qa|quality assurance|test engineer|sdet)\b') then 'qa'
    when regexp_matches({{ t }}, '\b(product manager|product owner|gerente de produto|program manager|project manager)\b') then 'product_project'
    when regexp_matches({{ t }}, '\b(designer|ux|ui|design lead)\b') then 'design'
    when regexp_matches({{ t }}, '\b(sales|account executive|business development|vendas|vendedor\w*|account manager|sdr|bdr)\b') then 'sales'
    when regexp_matches({{ t }}, '\b(marketing|growth|content|seo|brand)\b') then 'marketing'
    when regexp_matches({{ t }}, '\b(accountant|accounting|finance|financial|controller|auditor|payroll|contador\w*|financeiro)\b') then 'finance'
    when regexp_matches({{ t }}, '\b(recruiter|talent|human resources|hr|people (partner|operations)|recrutador\w*)\b') then 'hr'
    when regexp_matches({{ t }}, '\b(customer (support|service|success)|support (specialist|agent|representative)|atendimento|help desk)\b') then 'customer_support'
    when regexp_matches({{ t }}, '\b(nurse|rn|lpn|physician|therapist|clinical|medical|dental|pharmac\w*|caregiver|enfermeir\w*)\b') then 'healthcare'
    when regexp_matches({{ t }}, '\b(driver|warehouse|technician|mechanic|electrician|welder|operator|cashier|retail|store|cook|server|barista|janitor|laborer)\b') then 'operations_trades'
    else 'other'
end
{% endmacro %}

{% macro is_data_family(fam) %}
({{ fam }} in ('data_engineer', 'analytics_engineer', 'data_scientist', 'ml_engineer', 'data_analyst', 'data_architect'))
{% endmacro %}

{# Senioridade pelo titulo; vence a mais alta que casar. Sem sinal -> 'unspecified'. #}
{% macro seniority(t) %}
case
    when regexp_matches({{ t }}, '\b(intern|internship|estagi\w+|trainee|apprentice|co-?op)\b') then 'intern'
    when regexp_matches({{ t }}, '\b(chief|cto|cfo|ceo|coo|cio|cdo|vp|vice president|svp|evp|head of|diretor\w*|director)\b') then 'director'
    when regexp_matches({{ t }}, '\b(manager|gerente|supervisor\w*|coordenador\w*)\b') then 'manager'
    when regexp_matches({{ t }}, '\b(principal|staff|distinguished|fellow)\b') then 'staff_principal'
    when regexp_matches({{ t }}, '\b(lead|tech lead|team lead|lider)\b') then 'lead'
    when regexp_matches({{ t }}, '\b(senior|sr|snr|iii)\b') then 'senior'
    when regexp_matches({{ t }}, '\b(junior|jr|entry level|entry-level|graduate|new grad)\b') then 'junior'
    else 'unspecified'
end
{% endmacro %}
