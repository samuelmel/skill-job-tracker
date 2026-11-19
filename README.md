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
| `tests/integration` | Postgres real: migration, seed, ingestao, idempotencia, update, falha total, registro invalido |
| `tests/live` | marker `live`, ignorado por padrao; API real do Greenhouse |

Integracao usa `TEST_DATABASE_URL` se definida; caso contrario sobe um Postgres via `pgserver`.
Os payloads em `tests/fixtures` sao sinteticos (fixtures de teste) e **nao** contam como evidencia
de cobertura; a evidencia real vem dos testes `live` e da demo.

## Limitacoes conhecidas

- Apenas o conector Greenhouse existe; as demais 77 plataformas estao `NOT_STARTED`. Nenhuma esta `VERIFIED`.
- Greenhouse nao expoe salario estruturado, tipo de contrato nem modalidade: ficam `NULL` (nunca inferidos).
  `original_posted_at` vem de `first_published` e fica `NULL` quando ausente.
- Datas ISO sem fuso sao interpretadas como UTC; datas invalidas viram `NULL`.
- Sem deduplicacao entre fontes: cada `job_source` novo gera um job canonico 1:1
  (ponto de extensao: `IngestionService.resolve_job_identity`).
- Vagas que somem da listagem **nao** sao fechadas ainda.
- `pg_trgm` depende do servidor (ver acima). `search_vector` usa a configuracao `simple` (multilingue).
- Sem agendamento, API HTTP ou observabilidade alem de logs JSON.

## Fase 2 (planejada)

- Reconciliacao de vagas fechadas: so em run `SUCCEEDED` com `complete=True`, usando `missing_count`
  e `jobs_closed`.
- Deduplicacao cross-source (`dedup_matches`: fingerprint canonico, similaridade trigram, revisao).
- Normalizacao mais profunda (titulos, empresas/aliases, localizacao, salario).
- Novos conectores, agendamento (APScheduler) e API (Starlette).
