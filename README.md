# Vaya Job Board Indexer

Indexador de vagas de emprego em monolito modular. A **Fase 1** entrega a fundacao: schema PostgreSQL,
cliente HTTP resiliente, contrato de conectores, o conector **Greenhouse**, a ingestao idempotente
e o catalogo das 78 plataformas (58 ATS + 20 job boards).

## Arquitetura

```
src/vaya_indexer/
  core/          config (pydantic-settings), logging JSON, excecoes tipadas
  extraction/    http.py: HttpClient (retry, backoff+jitter, Retry-After, rate limit por dominio)
  connectors/    base.py (contrato), registry.py (@register), greenhouse.py
  processing/    normalization.py (titulo, empresa, espacos, datas, fingerprints)
  database/      models SQLAlchemy 2.0 async, session, repositories/
  ingestion/     service.py: IngestionService.run_source + register_source
  platforms_catalog.py   as 78 plataformas
  cli.py         seed-platforms, add-source, fetch, list-jobs
migrations/      Alembic (0001_initial)
```

Fluxo de `run_source`: cria `fetch_run` RUNNING -> conector busca (`RawJob` com payload intacto) ->
cada registro e normalizado/validado (erro isolado vai para `connector_errors` e `records_failed`) ->
upsert de `job_sources` por `(source_id, external_job_id)`; vaga nova cria job canonico 1:1; vaga
existente e comparada por `source_fingerprint` (sha256 dos campos normalizados) para contar
`updated` x `unchanged` (mudancas ficam em `job_history`) -> `fetch_run` finalizado com metricas.
Falha total de rede (ex.: `RetryExhausted`) marca a run como `FAILED` sem alterar nenhuma vaga.

## Requisitos

- Python >= 3.12 e [uv](https://docs.astral.sh/uv/)
- PostgreSQL 13+ (usa `gen_random_uuid()`), via Docker **ou** `pgserver` (embutido, dev)

## Instalacao

```bash
uv sync                 # inclui o grupo dev (pytest, ruff, mypy, respx, pgserver)
cp .env.example .env    # ajuste DATABASE_URL e USER_AGENT
```

Dependencias alem do exigido: nenhuma. `beautifulsoup4`, `lxml`, `apscheduler` e `starlette` estao
declaradas para as proximas fases e ainda nao sao usadas. `pgserver` e so dev (Postgres embutido
para testes e demo sem Docker).

> Windows: se o cache global do `uv` ficar em outra unidade que o projeto, defina
> `UV_CACHE_DIR` para uma pasta na mesma unidade (ou `UV_LINK_MODE=copy`).
> O driver async do psycopg exige `SelectorEventLoop`; a CLI e os testes ja cuidam disso.

## Banco de dados

**Docker:**

```bash
echo "POSTGRES_PASSWORD=troque-isto" >> .env
docker compose up -d postgres
# DATABASE_URL=postgresql+psycopg://vaya:troque-isto@localhost:5432/vaya
```

**Sem Docker (pgserver):** `uv run python scripts/demo_phase1.py` sobe um Postgres em `./.pgdata`
(ignorado no git) e roda a demo inteira. Para um banco novo, apague `./.pgdata`.

Migracoes:

```bash
uv run alembic upgrade head
```

Observacao: a migration cria a extensao `pg_trgm` e o indice trigram de `jobs.normalized_title`
apenas se a extensao estiver disponivel no servidor. O Postgres 16 oficial (Docker) a traz; o
`pgserver` embutido nao inclui contrib, entao nos testes locais esse indice e pulado (com aviso).

## CLI

```bash
uv run vaya seed-platforms
uv run vaya add-source --platform greenhouse --company "GitLab" --config '{"board_token":"gitlab"}'
uv run vaya fetch --source-id <uuid>
uv run vaya list-jobs --limit 10
```

(`python -m vaya_indexer ...` equivale a `vaya ...`.) `fetch` imprime o resumo da run em JSON e
retorna codigo 1 se a run falhou. Com Docker: `docker compose run --rm app seed-platforms`.

## Testes e qualidade

```bash
uv run pytest --cov=vaya_indexer      # unit + mocked + integration (sem rede)
uv run pytest -m live                 # Greenhouse real (rede)
uv run ruff check . && uv run ruff format --check .
uv run mypy src
```

| Pasta | O que cobre |
|---|---|
| `tests/unit` | normalizacao, catalogo de 78 plataformas, registry, config e normalize do Greenhouse |
| `tests/mocked` | HttpClient (retry 503, Retry-After no 429, 404 sem retry, timeout) e fetch do Greenhouse, com respx |
| `tests/integration` | Postgres real: migration (incl. 0001<->0002), seed, ingestao, dedup, reconciliacao, reabertura |
| `tests/live` | marker `live`, ignorado por padrao; API real do Greenhouse |

Integracao usa `TEST_DATABASE_URL` se definida; caso contrario sobe um Postgres via `pgserver`.
Os payloads em `tests/fixtures` sao sinteticos (fixtures de teste) e **nao** contam como evidencia
de cobertura; a evidencia real vem dos testes `live` e da demo.

## Fase 2: identidade, dedup e reconciliacao

**Normalizacao** (`processing/`, funcoes puras): `normalize_location` (+ `location_key`),
`infer_workplace_type`, `normalize_employment_type`, `parse_salary`, `canonical_url`, `html_to_text`.
Nada e inferido sem evidencia textual. Salario: nunca converte moeda nem periodo; ambiguo -> `NULL`.
Convencoes: `$` puro e lido como USD (descartado se houver codigo ISO ou simbolo forte); sem palavra de
periodo so assume `YEAR` se todos os valores usam sufixo `k` (>= 10k). `jobs.salary_period` guarda o periodo.
No Greenhouse o salario so vem de metadata de remuneracao e do blurb/titulo de `pay_input_ranges`.

**Dedup em camadas** (`processing/dedup.py` + `IngestionService.resolve_job_identity`): mesma
source+external_id; URL canonica (score 1.0); fingerprint; similaridade de titulo; veto por localizacao
conhecida e divergente (ou nivel de senioridade diferente); Jaccard de shingles da descricao.
Regras duras: nunca une empresas diferentes, nunca une dois `job_sources` da mesma source, nunca decide
so pelo titulo. Candidatos: mesma empresa, filtrados por fingerprint / URL canonica / titulo normalizado
(igual ou prefixo), `LIMIT 200`, com indices; ordenados por evidencia forte (fingerprint/URL), depois
titulo igual, depois prefixo, excluindo titulo normalizado vazio.

**Concorrencia**: persistencia + reconciliacao rodam sob `pg_try_advisory_xact_lock` por source; se outra
run da mesma source esta ativa, a run termina `FAILED` ("execucao concorrente em andamento") sem tocar dados.

| Variavel | Padrao | Efeito |
|---|---|---|
| `DEDUP_MERGE_THRESHOLD` | 0.90 | score >= limiar e sem veto: o novo `job_source` aponta ao job existente (`dedup_matches` MERGED) |
| `DEDUP_REVIEW_THRESHOLD` | 0.75 | entre revisao e merge: cria job novo e registra `dedup_matches` REVIEW |
| `DEDUP_URL_MIN_TITLE_SIMILARITY` | 0.6 | URL canonica so e evidencia forte se for especifica (id/slug da vaga, nao `/careers`, `/apply`) e o titulo for ao menos tao similar, com mesmo nivel |
| `RECONCILE_MAX_CLOSE_RATIO` | 0.5 | fracao de ausentes acima disso: nada e fechado, run `PARTIAL` |
| `RECONCILE_MIN_BLOCK_COUNT` | 5 | a guarda de razao so bloqueia se ausentes > max(este piso, razao x abertas); evita bloquear boards pequenos para sempre |
| `RECONCILE_MISSES_TO_CLOSE` | 2 | ausencias consecutivas para fechar |
| `RECONCILE_GRACE_HOURS` | 12 | `last_seen_at` precisa ser anterior a esta janela para fechar |

`dedup_matches`: MERGED = `job_id_a` (canonico), `job_id_b` NULL e `job_source_id` do source unido;
REVIEW = `job_id_a` (existente) e `job_id_b` (novo). Job canonico com varias fontes: a primeira fonte
manda nos campos, as demais so preenchem NULLs (com `job_history`); `OPEN` se algum `job_source` esta
`OPEN`, `CLOSED` so se todos estao `CLOSED`.

**Historico**: mudanca de status do job grava `job_history.field_name='status'`; mudanca de status de um
`job_source` grava `field_name='source_status'`; ambos com `fetch_run_id`. Vaga `CLOSED` que reaparece
reabre (`OPEN`, `missing_count=0`) e conta como `updated`.

**Reconciliacao segura** (`ingestion/reconciliation.py`): so roda sem excecao, com `complete=True` e
`records_failed=0`. Listagem vazia com vagas abertas -> nada fecha, run `PARTIAL` ("listagem vazia
suspeita"); fracao de ausentes acima do limite -> nada fecha, `PARTIAL`. Senao ausentes ganham
`missing_count += 1` e fecham ao atingir o minimo e a janela de graca; vistos zeram o contador. Runs
`FAILED`/`PARTIAL` nunca mexem em `missing_count`. `jobs_closed` conta jobs canonicos que passaram a `CLOSED`.

**Demo**: `uv run python scripts/demo_phase2.py` sobe Postgres em `./.pgdata-demo2`, recria o schema e
serve dados de **demonstracao controlados** num servidor HTTP local (config `api_base_url` do Greenhouse).
Nao e evidencia de cobertura. A demo da Fase 1 (`scripts/demo_phase1.py`) continua valida.

## Limitacoes conhecidas

- Apenas o conector Greenhouse existe; as demais 77 plataformas estao `NOT_STARTED`. Nenhuma esta `VERIFIED`.
- Greenhouse nao expoe salario estruturado: so texto explicito de metadata/`pay_input_ranges` e interpretado;
  tipo de contrato e modalidade so com evidencia textual. `original_posted_at` vem de `first_published`.
- Datas ISO sem fuso sao interpretadas como UTC; datas invalidas viram `NULL`.
- O fingerprint canonico agora usa `location_key`: dados da Fase 1 geram um `updated` unico na proxima coleta.
- Tabelas de paises/estados em `location.py` sao pequenas de proposito; texto fora delas fica sem estrutura.
- Dedup sem `pg_trgm` (indisponivel no pgserver): candidatos por igualdade/prefixo, nao por trigram.
  Casos REVIEW nao tem fluxo de resolucao ainda.
- `pg_trgm` depende do servidor (ver acima). `search_vector` usa a configuracao `simple` (multilingue).
- Sem agendamento, API HTTP ou observabilidade alem de logs JSON.

## Proximos passos

- Fluxo de resolucao de `REVIEW`, normalizacao de empresas/aliases, novos conectores,
  agendamento (APScheduler) e API (Starlette).
