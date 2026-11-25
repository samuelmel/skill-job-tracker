# Plano: de indexador de vagas a observatório do mercado de dados

Status: proposta, nada implementado. Base: código em `efd1aad` e perfil do banco `.pgdata-verify` em 2026-11-23.

## 1. Objetivo

Coletar vagas todo dia e responder, com dados históricos confiáveis:

- quais skills de dados estão subindo ou caindo
- quais empresas e setores estão contratando
- quanto tempo uma vaga fica aberta
- como remoto, senioridade e salário variam, quando houver amostra suficiente

O projeto passa a ser avaliado pela qualidade dessas respostas, e não pelo número de conectores.

## 2. Onde estamos

| Medida | Valor | O que significa |
|---|---|---|
| Plataformas VERIFIED | 43 de 78 | Cobertura suficiente para começar |
| Vagas / fontes / empresas | 17.689 / 106 / 106 | Amostra pequena de empresas |
| Títulos de dados | no máximo 127 (0,72%) | **Principal lacuna**; a contagem ainda inclui falsos positivos |
| Vagas com descrição | 60,3% | Limita a análise de skills |
| Vagas com salário | 5,9% | Análise salarial só como recorte secundário |
| Vagas com data de publicação | 67,9% | `first_seen_at` precisa complementar |
| Dias de coleta | 1 | **Ainda não existe série histórica** |

O que já está pronto e será reaproveitado:

- `ingestion/reconciliation.py` só fecha vagas em run limpa, com guardas de proporção, 2 ausências e 12 h de carência. Runs PARTIAL não fecham vagas.
- `job_history` registra mudanças por campo, e `fetch_runs` registra a qualidade de cada coleta.
- Dedup, normalização de salário e localização, e cobertura com evidência.

Lacunas no código:

- `job_sources.raw_data` é sobrescrito a cada update (`ingestion/service.py:605`), então o payload original se perde.
- Não há comando para coletar todas as fontes: a CLI só tem `fetch` de uma fonte.
- O rate limit do `HttpClient` vale por processo (`extraction/http.py:64`). Processos paralelos no mesmo domínio somariam requisições.
- Docker não está instalado nesta máquina. WSL2 com Ubuntu e 16 GB de RAM estão disponíveis.

## 3. Arquitetura alvo

```text
Conectores (43)
     │
     ├──► Raw imutável: data/raw/platform=/source=/dt=/run_id=/jobs.jsonl.gz + manifest.json
     │
     └──► Postgres (OLTP): estado atual, job_history, fetch_runs
                │
                ▼  exportação incremental
          Bronze em Parquet
                │
                ▼  dbt + DuckDB
          Silver (limpo, classificado) ──► Gold (marts)
                                              │
                                              ▼
                                       API + dashboard

Airflow orquestra coleta, exportação, dbt e checagens de qualidade.
```

## 4. Decisões

| Decisão | Escolha | Motivo |
|---|---|---|
| Formato raw | **JSONL.gz**, não Parquet | Cada plataforma tem um payload diferente; Parquet entra no Bronze |
| Histórico (SCD2) | **Derivar de `job_history`**, não usar dbt snapshot | O snapshot só vê o estado na hora em que roda; `job_history` já tem o momento exato |
| Fonte de verdade operacional | Postgres continua | Dedup, locks e reconciliação já dependem dele |
| Analítico local | dbt + DuckDB | Custo zero e cabe na máquina |
| Orquestrador | Airflow em container próprio, chamando a imagem do `skilometer` | Evita conflito de dependências com o projeto |
| Concorrência | Um pool do Airflow por plataforma/domínio | Compensa o rate limit por processo |
| Nuvem | Databricks só na Fase 6, opcional | Não resolve nenhuma lacuna atual |
| Novas plataformas | Pausadas | O gargalo é ter mais empresas de tecnologia nas plataformas que já funcionam |

## 5. Fases

### Fase 0: começar a acumular histórico (prioridade máxima)

Histórico perdido não se recupera, então esta fase vem antes de qualquer ferramenta nova.

- Instalar Docker no WSL e subir o Postgres do `docker-compose.yml` como banco de produção, separado dos bancos de verify.
- Gravar raw + manifest logo após `fetch_jobs` e antes de `_persist`. O manifest guarda `complete`, status, contagem e versão do conector.
- Falha ao gravar raw não bloqueia a ingestão, mas fica registrada e gera alerta.
- Criar `skilometer fetch-all`, com concorrência limitada por plataforma e sem interromper tudo quando uma fonte falha.
- Agendar uma coleta diária simples com cron no WSL. O Airflow assume na Fase 3.
- Carga mensal manual em um comando (coleta todas as fontes e depois bronze + dbt + dashboard): `uv run python scripts/run_monthly.py [--limit N] [--platform slug] [--skip-collect]`. Não refaz o dashboard se menos de 50% das fontes tentadas terminarem SUCCEEDED/PARTIAL.

**Pronto quando:** 7 dias seguidos de coleta, cada fonte com raw e `fetch_run`, e reconciliação sem fechamento em massa.

### Fase 1: fontes focadas em dados

- Criar um catálogo versionado de fontes (`empresa, plataforma, board`). Cada entrada só vale depois de um run real.
- Priorizar empresas de tecnologia em Greenhouse, Lever e Ashby, que têm API JSON pública.
- Medir por conector quanto vem com descrição e corrigir os que deixam de buscar o detalhe, respeitando robots.txt.
- Resolver o limite de 2.000 vagas do Workday com facets.

**Pronto quando:** pelo menos 500 fontes ativas, 2.000 vagas abertas de dados e 85% de descrição nas vagas de dados.

### Fase 2: modelagem com dbt

- **Bronze:** exportar tabelas do Postgres para Parquet por `updated_at`/`detected_at`.
- **Silver:** `dim_company`, `dim_platform`, `fct_job`, `fct_job_version` (SCD2 a partir de `job_history`), classificação de família de cargo e senioridade.
- **Skills:** dicionário versionado em seed, com aliases e limite de palavra (`etl` não pode casar com "Retail").
- **Gold:** `mart_skill_demand_weekly`, `mart_hiring_by_company`, `mart_time_to_close`, `mart_salary`, `mart_source_health`.

Regras analíticas obrigatórias:

- Percentual de skill usa como denominador apenas vagas com descrição.
- Tempo até fechar usa só fechamentos vindos de run limpa; vagas ainda abertas entram como censuradas.
- Salário nunca mistura moeda ou período, e só publica grupos com n ≥ 30.
- Tendência compara o mesmo conjunto de fontes, para uma fonte nova não parecer alta do mercado.

**Pronto quando:** `dbt build` passa do zero, com testes de unicidade, nulos, relacionamentos e frescor, e uma amostra rotulada de 200 títulos atinge precisão ≥ 90% na classificação.

### Fase 3: orquestração com Airflow

- DAG `ingest_daily`: uma task por fonte (dynamic task mapping), com pools por plataforma.
- DAG `transform_daily`: exportação, `dbt build` e checagens, disparada por Asset quando a ingestão termina.
- Backfill reprocessando a partir do raw, sem novas requisições aos sites.
- Alertas para queda brusca de vagas, fonte sem sucesso há 3 dias e aumento de PARTIAL.

**Pronto quando:** 14 dias sem intervenção manual e um backfill de 7 dias com resultado idêntico ao original.

### Fase 4: API e dashboard

- API em Starlette (já é dependência), lendo da camada Gold.
- Dashboard em tela cheia com tendência de skills, empresas contratando, mapa de senioridade e saúde das fontes.
- Público: dados agregados, trechos curtos e link para a vaga original. Sem republicar descrições inteiras.

**Pronto quando:** responde às perguntas do objetivo em desktop e celular.

### Fase 4.5: automação mensal e publicação

Nada aqui foi executado. Renomear, criar o remoto, dar push e publicar dependem de aprovação explícita.

1. **Repositórios (decidido):** o código vai para `skilometer`, público para o Pages, e os dados para `skilometer-data`, **privado**. Pacote, CLI e documentação usam o mesmo nome (`skilometer`). Hoje não há remoto, e a branch local é `master`.
2. **Antes do primeiro push:** revisar o histórico em busca de segredos e confirmar que `data/`, `.pgdata*` e arquivos de rascunho continuam fora do Git.
3. **Workflow mensal:** `.github/workflows/monthly.yml` com `schedule` (dia 1, em minuto fora da hora cheia) e `workflow_dispatch`. Roda `scripts/run_monthly.py` com Postgres embutido, `concurrency` para impedir duas cargas juntas, `timeout-minutes` abaixo do limite de 6 horas e `permissions` mínimas por job.
4. **Estado entre execuções (decidido):** o runner é descartável e `data/` não vai para o repositório de código. O raw imutável e o manifesto, incluindo o histórico do Hacker News, ficam em `skilometer-data`. Bronze e warehouse são derivados e reconstruídos no CI, para o repositório privado não crescer à toa. O workflow acessa esse repositório com uma deploy key ou token de escopo mínimo guardado em GitHub Secrets. Acompanhar o tamanho a cada mês (o raw atual tem cerca de 16 MB por coleta) e rever a estratégia antes de chegar a 1 GB. Cache do Actions não serve como fonte de verdade, e Release em repositório público exporia as descrições completas.
5. **Portabilidade:** remover o caminho absoluto da view `silver_hn_post` e reconstruir o warehouse a partir do Bronze no CI.
6. **Publicação:** job separado envia ao GitHub Pages somente o HTML estático agregado. Sem raw, descrição completa, autor ou banco. O bloqueio atual (menos de 50% das fontes com sucesso) impede o deploy e mantém a última versão boa no ar.
7. **Coleta em datacenter:** medir na primeira execução quais fontes bloqueiam IPs do GitHub. Fonte bloqueada fica registrada como falha, sem contornar robots.txt, Cloudflare ou CAPTCHA. Se a cobertura cair demais, coletar localmente e deixar o Actions só com transformação e publicação.

**Pronto quando:** um disparo manual e uma execução agendada publicam o dashboard no Pages, o mês aparece na série e uma falha simulada não derruba a versão publicada.

### Fase 5: portfólio

- README com arquitetura, decisões, trade-offs e métricas reais.
- Demo reproduzível com amostra pequena e um `make demo`.
- Artigo com a primeira análise, quando houver pelo menos 60 dias de histórico.

### Fase 6: nuvem (opcional)

- Levar os modelos para Databricks com `dbt-databricks` e o raw para object storage.
- Só fazer se houver um motivo concreto, como vaga-alvo pedindo a ferramenta ou volume que o DuckDB não aguente.

## 6. Riscos

| Risco | Mitigação |
|---|---|
| Computador desligado cria buracos na série | Registrar dias sem coleta; migrar para uma VPS pequena se virar rotina |
| Sites mudam e conectores quebram | `mart_source_health`, alertas e contract tests |
| Viés: a amostra não representa o mercado inteiro | Publicar a composição das fontes e falar em "vagas monitoradas" |
| Sites bloqueiam IPs do GitHub Actions | Medir a cobertura no CI, respeitar o bloqueio e, se preciso, manter a coleta local |
| GitHub desativa `schedule` após 60 dias sem atividade em repositório público | Monitorar a execução mensal e manter `workflow_dispatch` para rodar manualmente |
| Dados brutos expostos ao publicar | Estado em armazenamento privado; Pages recebe só o HTML agregado |
| Raw cresce sem controle | Compressão e política de retenção depois de medir o volume da Fase 0 |
| Termos de uso e dados pessoais | Respeitar robots.txt, não coletar dados de candidatos, não republicar descrições |
| Complexidade maior que o valor | Cada fase precisa entregar algo utilizável antes da próxima |

## 7. Fora de escopo por enquanto

- As 33 plataformas ainda não iniciadas
- Fallback de HTML do JobBoard.io (4 vagas conhecidas)
- Snowflake, Kafka e streaming: coleta diária não precisa deles
- Modelos de ML ou LLM para classificar vagas, até o dicionário mostrar seu limite
