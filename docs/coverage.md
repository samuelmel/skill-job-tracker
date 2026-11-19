# Cobertura das plataformas

Gerado em 2026-11-19 19:12 UTC por `vaya coverage-report`. So conta como **VERIFIED** a plataforma com execucao real bem-sucedida (run SUCCEEDED com vagas); fixtures sinteticas e testes mockados nao contam.

**13 de 78 plataformas com evidencia real.**

## Totais por status

| Status | Plataformas |
|---|---|
| NOT_STARTED | 63 |
| RESEARCHING | 0 |
| IMPLEMENTED | 0 |
| TESTED | 1 |
| VERIFIED | 13 |
| BLOCKED | 1 |

## O que falta

- **NOT_STARTED** (63): ADP, ApplicantPro, Avature, BrassRing, CareerPlug, Comeet, CSOD, Dayforce, Deel, Dover, Eightfold AI, FirstStage, Freshteam, GoHire, HiBob, HireBridge, Hireology, HiringThing, iCIMS, iSolvedHire, JazzHR, Jobvite, JOIN.com, Kula.ai, Oracle Recruiting, PageUp, Paradox, Paycom, Paycor, Paylocity, Personio, Phenom People, Recooty, Rival, SAP SuccessFactors, talentReef, Taleo, Teamtailor, Trakstar, TriNet, UltiPro, WeRecruit, Zoho Recruit, Artha, CareerSiteCloud, eJobBoard, ejobsitesoftware.com, JBoard.io, JobBoard.com, JobBoard.io, JobBoardFire, jobBoardHQ, Jobboardly, jobboardsoftware.com, Jobiqo, JobKit, JobMount, Niceboard, Nodex, SmartJobBoard, Talenetic, Wiley Madgex, WP Job Manager
- **TESTED** (1): Workday
- **BLOCKED** (1): SmartRecruiters

## Matriz

| Plataforma | Categoria | Status | Estrategia | Instancias (ok/config.) | Ultima verificacao | Vagas | Amostra (evidencia) | Limitacoes |
|---|---|---|---|---|---|---|---|---|
| ADP | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| ApplicantPro | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Ashby | ATS | VERIFIED | API JSON publica (api.ashbyhq.com/posting-api/job-board/{org_slug}?in… | 2/2 | 2026-11-19 19:07 UTC | 351 | [Contract] Language Training Specialist - Japanese; Account Executive; AI Product Manager, Growth Marketing; Account Executive \| Commercial; Brand Designer, Creative Studio | Respostas grandes (descricao inline; ate ~14MB em quadros como o da O… |
| Avature | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| BambooHR | ATS | VERIFIED | Endpoint publico da pagina de carreiras ({subdomain}.bamboohr.com/car… | 2/2 | 2026-11-19 19:07 UTC | 49 | Assistant Service Operations Engineer; Winter / Spring Intern - Major Gifts; Business Development Executive; Winter / Spring Intern - Social Media; Collections Coordinator | Endpoint nao documentado. Subdominios sem pagina de carreiras respond… |
| BrassRing | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Breezy HR | ATS | VERIFIED | Feed JSON publico da pagina de vagas ({company}.breezy.hr/json), list… | 3/3 | 2026-11-19 19:07 UTC | 45 | Assistant Director of Care: London, Ontario; BBS Practitioner Global Grade 09; 1 Demo/Testing Position - Clone 12/18/2014; Dietary Aide; Condition Monitor Global Grade 09 | Endpoint nao documentado; nem todo tenant habilita o feed (404). Sem … |
| CareerPlug | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Comeet | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| CSOD | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Dayforce | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Deel | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Dover | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Eightfold AI | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| FirstStage | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Freshteam | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Gem.com | ATS | VERIFIED | Job Board API publica (api.gem.com/job_board/v0/{board_slug}/job_post… | 2/2 | 2026-11-19 19:08 UTC | 83 | Engineering Manager (AI Products); Administrative Coordinator, EPD; Baremetal Infrastructure Engineer; BizOps – GTM & Monetization; Business Development Lead - Air Force | Slug inexistente responde 404. Sem campo de salario (so no texto da d… |
| GoHire | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Greenhouse | ATS | VERIFIED | API JSON publica do Job Board (boards-api.greenhouse.io/v1/boards/{bo… | 4/4 | 2026-11-19 19:12 UTC | 1248 | Abuse Investigator; Account Executive, Mobile Gaming & Apps; Account Executive (Hotels); AI Transformation Owner, CRO; Account Executive- Enterprise Grower | Apenas boards publicos com board_token conhecido; salario so quando h… |
| HiBob | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| HireBridge | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| HireHive | ATS | VERIFIED | Jobs API v2 publica ({subdomain}.hirehive.com/api/v2/jobs), paginada … | 3/3 | 2026-11-19 19:08 UTC | 78 | Account Executive; (Senior) Pre Sales Consultant – EHS & ESG (m/f/d) - Dutch s…; Applications Functional Support Analyst - MS Dynamics 365 F…; Accounts Payable Administrator; Account Executive | Termos de uso do HireHive nao revisados. Salario so quando preenchido… |
| Hireology | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| HiringThing | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| iCIMS | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| iSolvedHire | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| JazzHR | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Jobvite | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| JOIN.com | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Kula.ai | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Lever.co | ATS | VERIFIED | Postings API publica (api.lever.co/v0/postings/{company}?mode=json; t… | 3/3 | 2026-11-19 19:07 UTC | 405 | Administrative Business Partner; API Engineer; Account Executive - Commercial, Spotify Technology; Administrative Business Partner; BI Developer | A regiao (global\|eu) de cada tenant precisa ser conhecida; slug inex… |
| Manatal | ATS | VERIFIED | API da pagina publica careers-page.com (api.manatal.com/open/v3/caree… | 3/3 | 2026-11-19 19:08 UTC | 57 | Optometrist; BUYER/TRADER; AI Content Marketing Internship; Parts Salesman; Certified Nurse Midwife - Full time | Endpoint observado no site publico, nao documentado (a API oficial ex… |
| Oracle Recruiting | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| PageUp | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Paradox | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Paycom | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Paycor | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Paylocity | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Personio | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Phenom People | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Pinpoint | ATS | VERIFIED | Feed publico {subdomain}.pinpointhq.com/postings.json, lista completa… | 2/2 | 2026-11-19 19:08 UTC | 190 | After-sales Key Account Executive, Cotonou (Benin Republic); Senior Product Marketing Manager; After-Sales Manager - Cotonou (Benin Republic); Senior Technical Success Engineer; After Sales - Manager, Togo | Sem data de publicacao (so deadline_at). Remuneracao so quando compen… |
| Polymer | ATS | VERIFIED | API usada pela pagina jobs.polymer.co (api.polymer.co/v1/hire/organiz… | 3/3 | 2026-11-19 19:08 UTC | 32 | Accounts Payable Assistant (Entry Level); Account Executive, AI Compute; Contractor Sales Lead (Colorado); Chat &amp; Email Support Agent; Business Operations Associate | API nao documentada. Sem descricao na listagem (so na pagina da vaga)… |
| Recooty | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Recruitee | ATS | VERIFIED | Careers Site API publica ({company}.recruitee.com/api/offers/), lista… | 3/3 | 2026-11-19 19:07 UTC | 33 | Head of Growth & Product; Academic Counselor for MiaPrep Online High School (MOHS); (Senior) Product Owner; Learning Experience Designer; Administrative Support Associate for MiaPrep Online Academy… | Subdominio inexistente responde 404; quadro vazio devolve offers=[]. |
| Rippling | ATS | VERIFIED | API publica do quadro ats.rippling.com (api.rippling.com/platform/api… | 3/3 | 2026-11-19 19:08 UTC | 51 | Product Delivery Manager; Bilingual Japanese Analyst, Merchant Monitoring; Call List; Senior Product Manager, AI Sales Platform; Director, Data Product and Growth Strategy | API nao documentada. Listagem so com nome, departamento, URL e local:… |
| Rival | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| SAP SuccessFactors | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| SmartRecruiters | ATS | BLOCKED | Posting API (api.smartrecruiters.com/v1/companies/{id}/postings?limit… | 0/0 | nunca | - | - | BLOCKED por robots.txt: https://api.smartrecruiters.com/robots.txt de… |
| talentReef | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Taleo | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Teamtailor | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Trakstar | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| TriNet | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| UltiPro | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| WeRecruit | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Workable | ATS | VERIFIED | Endpoint publico v3 da pagina de vagas (POST apply.workable.com/api/v… | 3/3 | 2026-11-19 19:07 UTC | 224 | Agentic AI Architect; Head of Finance; Regional Marketing Manager, Northern Europe; AI and Integration Architect; Nurse Practitioner GLP-1 Prescriber (1099) | Endpoint nao documentado (usado pela SPA). A listagem nao traz descri… |
| Workday | ATS | TESTED | Endpoint CXS publico da pagina de carreiras (POST {tenant}.{host}.myw… | 0/2 | nunca (ultima tentativa falhou: listagem incompleta) | - | - | Endpoint interno nao documentado; tenant, host (wdN) e site vem da UR… |
| Zoho Recruit | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Artha | JOB_BOARD | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| CareerSiteCloud | JOB_BOARD | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| eJobBoard | JOB_BOARD | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| ejobsitesoftware.com | JOB_BOARD | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| JBoard.io | JOB_BOARD | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| JobBoard.com | JOB_BOARD | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| JobBoard.io | JOB_BOARD | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| JobBoardFire | JOB_BOARD | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| jobBoardHQ | JOB_BOARD | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Jobboardly | JOB_BOARD | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| jobboardsoftware.com | JOB_BOARD | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Jobiqo | JOB_BOARD | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| JobKit | JOB_BOARD | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| JobMount | JOB_BOARD | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Niceboard | JOB_BOARD | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Nodex | JOB_BOARD | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| SmartJobBoard | JOB_BOARD | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Talenetic | JOB_BOARD | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Wiley Madgex | JOB_BOARD | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| WP Job Manager | JOB_BOARD | NOT_STARTED | - | 0/0 | nunca | - | - | - |
