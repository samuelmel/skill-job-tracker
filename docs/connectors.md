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

## Conectores do lote 2B (feeds, sitemap + JSON-LD e HTML publico)

Sem autenticacao e com o `HttpClient` do projeto. Onde nao ha total declarado a coleta nao
consegue provar integridade, entao teto de vagas ou item descartado resulta em `complete=False`.

| Conector (`connector_type`) | Config | Fonte | Limitacoes |
|---|---|---|---|
| `personio` | `account`, `tld` (`com`/`de`) | feed XML `{account}.jobs.personio.{tld}/xml` | sem salario |
| `pageup` | `client_id`, `lang` | RSS `careers.pageuppeople.com/{id}/cw/{lang}/rss` (ext. `job:*`) | id numerico do cliente |
| `hiringthing` | `subdomain` | RSS `{sub}.hiringthing.com/api/rss.xml` | sem data nem salario |
| `teamtailor` | `company` | RSS `{company}.teamtailor.com/jobs.rss` | Content-Signal `ai-train=no`: so indexacao |
| `isolvedhire` | `subdomain` | `/job_site_map.xml` + JSON-LD em `/jobs/{id}.html` | ate 200 vagas; salario descartado |
| `trakstar` | `company` | `{co}.hire.trakstar.com/sitemap.xml` + JSON-LD | ate 500 vagas |
| `hireology` | `slug` | HTML `careers.hireology.com/{slug}?page=N` | seletores `data-qa-id`; sem descricao |
| `recooty` | `company` | HTML `jobs.recooty.com/{co}?page=N` + JSON-LD no detalhe | seletores frageis; ate 200 vagas |
| `trinet` | `company` (`{id}-{slug}`) | HTML `app.trinethire.com/companies/{id}-{slug}/jobs` + detalhe | `ai-train=no` no robots: so indexacao; ate 100 vagas |
| `zoho-recruit` | `company` | `{co}.zohorecruit.com/jobs/Careers` (`input#jobs`) | so dominio `.com`; alguns tenants sem descricao |
| `phenom-people` | `domain`, `lang`, `country`, `page_id` | POST `{domain}/widgets` (`refineSearch`) | so `descriptionTeaser`; endpoint nao documentado |

`feeds.parse_xml`/`FeedEntry.element` e o fallback `strict=False` de `jsonld.py` foram
estendidos de forma aditiva para estes conectores.

## Conectores do lote 3A (job boards: sitemap + JSON-LD e feed WP Job Manager)

Sem autenticacao e com o `HttpClient`. Helpers em `connectors/_boards3a.py`. O RSS de
SmartJobBoard e JobBoard.io e truncado pela plataforma: a lista completa vem do sitemap + JSON-LD
de cada vaga, e o RSS so e fallback (sempre `complete=False`).

| Conector (`connector_type`) | Config | Fonte | Limitacoes |
|---|---|---|---|
| `niceboard` | `board_domain` | `/sitemap-jobs.xml` + JSON-LD em `/job/{id}-{slug}`; `apply_url` de `var job_json` | Crawl-delay 1s; ate 200 vagas |
| `wp-job-manager` | `base_url` (https, pode ter subcaminho) | RSS `{base}/?feed=job_feed&posts_per_page=100&paged=N` | ate 20 paginas; sites que ignoram `paged` (jobicy) = `complete=False`; sem link externo de candidatura |
| `smartjobboard` | `board_domain` | `/sitemap.xml` + JSON-LD em `/job/{id}/{slug}/`; RSS `/rss` so fallback | ate 300 vagas; `/application-redirect/` proibido no robots |
| `jobboard-io` | `board_domain` | indice `/sitemap.xml` -> `/sitemaps/jobs/*` + JSON-LD em `/jobs/{id}-{slug}`; RSS `/jobs.rss` so fallback | ate 300 vagas; distinto de jboard.io |

## Conectores do lote 3b (quadros de vagas: RSS, sitemap + JSON-LD e postback ASP.NET)

Todos usam `board_domain` (host do quadro) e o `HttpClient` do projeto. Os que leem uma pagina por
vaga respeitam o `Crawl-delay` do robots.txt com uma pausa entre paginas e um teto de vagas
(`complete=False` quando o teto corta a listagem ou um detalhe falha).

| Conector (`connector_type`) | Config | Fonte | Limitacoes |
|---|---|---|---|
| `jobboardly` | `board_domain` | RSS `/jobs.rss` (um request; `job:city/state/country`; titulo 'cargo - empresa - local') | sem salario nem tipo de contrato |
| `jobiqo` | `board_domain` | `/sitemap.xml` (filtra `/job/{slug}-{id}`) + JSON-LD (descricao com HTML escapado) | Crawl-delay 10s; ate 200 vagas (lastmod mais recente); candidatura = URL da vaga |
| `jboard-io` | `board_domain` | indice `/sitemap.xml` -> `sitemap-jobs-N.xml` + JSON-LD em `/jobs/{id}-{slug}`; candidatura `/jobs/{id}/apply` | `/rss/` proibido; Crawl-delay 1s; ate 1000 vagas; distinto de `jobboard-io` |
| `jobboardhq` | `board_domain` | `/search` paginado por postback ASP.NET (`__VIEWSTATE`, `__EVENTTARGET=Page`) + JSON-LD | `?page=N` nao pagina; busca limitada a 500/1000 pelo quadro (`complete=False`); ate 600 vagas |

## Conectores do lote 2A (APIs JSON publicas e HTML de portais de ATS)

Sem autenticacao e com o `HttpClient` do projeto. Quando a fonte declara o total, `complete` so
e `True` se o numero de vagas unicas coletadas o iguala; pagina desconhecida (login, redirect,
layout via JS) e `ParseError`, nunca lista vazia "completa".

| Conector (`connector_type`) | Config | Fonte | Limitacoes |
|---|---|---|---|
| `comeet` | `company_uid`, `careers_url` | `careers-api/2.0/company/{uid}/positions`; token publico lido em runtime da pagina de carreiras | token nunca guardado; sem `original_posted_at` |
| `applicantpro` | `subdomain`, `domain_id` | `{sub}.applicantpro.com/core/jobs/{id}` | teto de 300 vagas (`complete=False` se `jobCount` maior); sem descricao/salario |
| `careerplug` | `subdomain` | HTML `/jobs` com JSON-LD `ItemList` | `/jobs.js` e `/j/` proibidos no robots; quadro vazio vira "sem vagas" |
| `eightfold_ai` | `tenant`, `domain` | `api/apply/v2/jobs?domain=` (10 por pagina) | sem descricao na listagem |
| `join_com` | `company` | `__NEXT_DATA__` de `join.com/companies/{company}` | `?page=N` nao verificado; total divergente = `complete=False` |
| `jobvite` | `company` | HTML `jobs.jobvite.com/{co}/jobs` (`table.jv-job-list`) | portais com lista via JS (ex.: innio) nao suportados |
| `icims` | `host` | HTML `{host}/jobs/search?ss=1&in_iframe=1&pr=N` | layout `iCIMS_JobCardItem`; sem descricao |
| `oracle_recruiting` | `host`, `site_number` | REST `recruitingCEJobRequisitions` (finder `findReqs`) | WAF (ex.: jpmc) bloqueia; paginas curtas intermediarias; descricao so resumo |
| `jazzhr` | `subdomain` | HTML `{sub}.applytojob.com/apply` | subdominio inexistente redireciona e vira `ParseError` |
| `paylocity` | `guid` | `window.pageData.Jobs` de `recruiting.paylocity.com/recruiting/jobs/All/{guid}` | descricao truncada fica None; vagas internas ignoradas |
| `avature` | `host`, `path` | feed RSS `.../SearchJobs/feed/` + total do HTML | feed limitado pelo portal (Bloomberg 20 de 336 = parcial) |
