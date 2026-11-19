# Como adicionar um conector

Este guia cobre o ciclo completo de um conector novo: codigo, registro, configuracao,
fixture sintetica, entrada em `platforms.json` e verificacao com evidencia real.

Cobertura so conta com evidencia real: uma plataforma so vira `VERIFIED` quando
`vaya verify` extrai pelo menos uma vaga de uma instancia publica real.

## 1. Arquivo do conector

Crie `src/vaya_indexer/connectors/<nome>.py` com uma subclasse de `BaseConnector`:

```python
@register("minha_plataforma")
class MinhaPlataformaConnector(BaseConnector):
    platform_name = "minha_plataforma"
    config_model = MinhaConfig  # pydantic, ex.: board_token
    fixture_path = Path(__file__).parent / "fixtures" / "minha_plataforma.json"
    requires_browser = False  # True so se HTTP puro nao basta
```

Metodos exigidos: `external_id_from_config`, `fetch` (retorna `FetchResult` com
`complete=False` quando a coleta foi truncada) e `normalize` (`RawJob` -> `NormalizedJob`).
Use o `HttpClient` injetado (`self.http`): ele aplica retry, rate limit e User-Agent
identificavel. Nao contorne bloqueios nem faca stealth.

## 2. Registro

O decorator `@register("nome")` registra a classe. Importe o modulo em
`registry._ensure_loaded` para que `registered_names()` o enxergue.

## 3. Configuracao

`config_model` e um modelo pydantic validado por `validate_config`. Cada instancia de
empresa na plataforma e uma configuracao (ex.: `{"board_token": "gitlab"}`).

## 4. Fixture sintetica

Crie `connectors/fixtures/<nome>.json` com `"synthetic": true` e `raw_jobs` (lista de
`RawJob`, `external_id` unicos). Ela e dado inventado, usado apenas pelo teste de
contrato e para decidir entre `IMPLEMENTED` e `TESTED`. Nunca conta como cobertura.

## 5. Entrada em platforms.json

Em `src/vaya_indexer/coverage/platforms.json`, na chave do slug da plataforma:

- `connector_type`: o nome registrado.
- `example_instances`: `[{"company": "...", "config": {...}}]` com instancias reais e publicas.
- `docs_urls`, `extraction_strategy`, `limitations`.
- `research_status`: `NOT_STARTED`, `RESEARCHING` ou `BLOCKED` (so vale sem conector).

Rode `vaya seed-platforms` para sincronizar com o banco. O seed nunca sobrescreve
`IMPLEMENTED`, `TESTED` ou `VERIFIED`.

## 6. Verificacao

```
vaya verify --platform minha_plataforma
vaya verify --all --concurrency 3
vaya coverage-report
```

`verify` registra as fontes de forma idempotente, executa o `IngestionService.run_source`
real e grava uma linha em `platform_verifications` (append-only).

Regras de status (refletem a ULTIMA verificacao):

- Qualquer instancia com run `SUCCEEDED` e `jobs_found >= 1`, sem falha total de
  normalizacao: `VERIFIED`.
- So falhas transitorias (rede, `RetryExhausted`, `BrowserUnavailable`, run concorrente):
  status inalterado; evidencia anterior permanece.
- Falha real (quadro vazio, 404, parse): `TESTED` se a fixture sintetica normaliza,
  senao `IMPLEMENTED`.
- Plataforma sem conector: nada e alterado.

`coverage-report` gera `docs/coverage.md` e `docs/coverage.json` a partir do banco.

## 7. Kit de utilitarios

- `connectors/pagination.py`: `paginate`, `paginate_offset`, `paginate_pages` com
  `max_pages`; ao atingir o limite (ou repetir pagina) retorna `complete=False`.
- `connectors/jsonld.py`: `extract_job_postings(html)` e `job_posting_to_normalized`.
- `connectors/feeds.py`: `parse_feed(xml)` para RSS 2.0 e Atom (lxml endurecido).
- `extraction/browser.py`: `BrowserFetcher` (Playwright headless, timeout, UA
  identificavel, sem stealth). Use apenas com `requires_browser = True`.

## 8. Navegador opcional

```
uv sync --extra browser
uv run playwright install chromium
```

Sem o extra, conectores com `requires_browser = True` falham com `BrowserUnavailable`
(erro tipado, classificado como transitorio).

## 9. Teste de contrato

`tests/contract/test_connector_contract.py` roda sobre todos os conectores registrados:
nome unico, declarado em `platforms.json`, config valida nas instancias de exemplo e
fixture sintetica que normaliza. Um conector novo entra automaticamente.

## 10. Conectores do lote 1 (APIs publicas de ATS)

Todos usam o `HttpClient` do projeto (UA identificavel, limite por dominio), sem autenticacao.
Campos que a listagem nao traz ficam `None`; `complete=False` sempre que a listagem pode
estar truncada (limite de paginas, total divergente, item sem identidade).

| Conector (`connector_type`) | Config | Endpoint | Paginacao | Limitacoes |
|---|---|---|---|---|
| `ashby` | `org_slug` | GET `api.ashbyhq.com/posting-api/job-board/{org}` (`includeCompensation=true`) | lista unica | so vagas `isListed` |
| `lever_co` | `company`, `region` (`global`/`eu`) | GET `api.lever.co/v0/postings/{company}?mode=json` | listagem completa | external_id da instancia EU: `eu:{company}` |
| `recruitee` | `company` | GET `{company}.recruitee.com/api/offers/` | lista unica | - |
| `workable` | `account` | POST `apply.workable.com/api/v3/accounts/{account}/jobs` | cursor `token`/`nextPage` | sem descricao |
| `breezy_hr` | `company` | GET `{company}.breezy.hr/json` | lista unica | sem descricao |
| `bamboohr` | `subdomain` | GET `{sub}.bamboohr.com/careers/list` | lista unica, confere `meta.totalCount` | sem descricao |
| `gem_com` | `board_slug` | GET `api.gem.com/job_board/v0/{slug}/job_posts/` | lista unica | - |
| `pinpoint` | `subdomain` | GET `{sub}.pinpointhq.com/postings.json` | lista unica | salario so se `compensation_visible` |
| `polymer` | `org` | GET `api.polymer.co/v1/hire/organizations/{org}/jobs?page=N` | `meta.is_last` | sem descricao; paginacao so testada com mock |
| `rippling` | `board` | GET `api.rippling.com/platform/api/ats/v1/board/{board}/jobs` | lista unica | so titulo, local e URL |
| `hirehive` | `subdomain` | GET `{sub}.hirehive.com/api/v2/jobs?page=N&page_size=100` | `meta.has_next_page` | - |
| `manatal` | `slug` | GET `api.manatal.com/open/v3/career-page/{slug}/jobs/?page=N` | `next` so como sinal; confere `count` | endpoint nao documentado; salario nao exposto |
| `workday` | `tenant`, `host` (`wdN`), `site` | POST `{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` | offset/limit (20); `total` so na pagina 1 | sem descricao nem data (so texto relativo) |

Valores de config sao validados por regex (`connectors/_common.py`) para impedir que um
slug altere o host da requisicao.

### SmartRecruiters: bloqueado

Nao implementado. O `robots.txt` de `api.smartrecruiters.com` tem `User-agent: *` /
`Disallow: /` e libera apenas o LinkedInBot em `/v1/companies/`. Como o projeto respeita
robots.txt, a plataforma esta `BLOCKED` em `platforms.json` ate haver acordo/parceria.
