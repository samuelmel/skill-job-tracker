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
