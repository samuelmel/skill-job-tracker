# Cobertura das plataformas

Gerado em 2026-11-19 19:45 UTC por `vaya coverage-report`. So conta como **VERIFIED** a plataforma com execucao real bem-sucedida (run SUCCEEDED com vagas); fixtures sinteticas e testes mockados nao contam.

**35 de 78 plataformas com evidencia real.**

## Totais por status

| Status | Plataformas |
|---|---|
| NOT_STARTED | 41 |
| RESEARCHING | 0 |
| IMPLEMENTED | 0 |
| TESTED | 1 |
| VERIFIED | 35 |
| BLOCKED | 1 |

## O que falta

- **NOT_STARTED** (41): ADP, BrassRing, CSOD, Dayforce, Deel, Dover, FirstStage, Freshteam, GoHire, HiBob, HireBridge, Kula.ai, Paradox, Paycom, Paycor, Rival, SAP SuccessFactors, talentReef, Taleo, UltiPro, WeRecruit, Artha, CareerSiteCloud, eJobBoard, ejobsitesoftware.com, JBoard.io, JobBoard.com, JobBoard.io, JobBoardFire, jobBoardHQ, Jobboardly, jobboardsoftware.com, Jobiqo, JobKit, JobMount, Niceboard, Nodex, SmartJobBoard, Talenetic, Wiley Madgex, WP Job Manager
- **TESTED** (1): Workday
- **BLOCKED** (1): SmartRecruiters

## Matriz

| Plataforma | Categoria | Status | Estrategia | Instancias (ok/config.) | Ultima verificacao | Vagas | Amostra (evidencia) | Limitacoes |
|---|---|---|---|---|---|---|---|---|
| ADP | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| ApplicantPro | ATS | VERIFIED | Endpoint JSON da pagina de carreiras (`/core/jobs/{domainId}`), sem a… | 2/3 | 2026-11-19 19:41 UTC (ultima tentativa falhou: listagem incompleta) | 219 | API Inspector (510/570/653) - Beaumont, TX; Active Adult Facilitator- All Locations; API Inspector (650 / 653) - Westlake, LA; Aquatics Coordinator - Midlothian; Boilermaker - Lake Charles, LA | Endpoint limitado a 300 vagas: quando `jobCount` excede o devolvido, … |
| Ashby | ATS | VERIFIED | API JSON publica (api.ashbyhq.com/posting-api/job-board/{org_slug}?in… | 2/2 | 2026-11-19 19:07 UTC | 351 | Account Executive; [Contract] Language Training Specialist - Japanese; Account Executive \| Commercial; AI Product Manager, Growth Marketing; Account Executive \| Enterprise | Respostas grandes (descricao inline; ate ~14MB em quadros como o da O… |
| Avature | ATS | VERIFIED | Feed RSS publico `{host}/{path}/SearchJobs/feed/` + total do HTML `Se… | 1/2 | 2026-11-19 19:41 UTC | 14 | Backend Software Engineer (Argentina); Backend Software Engineer, DevOps (Argentina); Cloud Engineer (Argentina); Configuration Quality Engineer, CQE (Argentina); Fullstack Software Engineer (Argentina) | O feed e limitado pelo portal (Bloomberg: 20 de 336): complete=False … |
| BambooHR | ATS | VERIFIED | Endpoint publico da pagina de carreiras ({subdomain}.bamboohr.com/car… | 2/2 | 2026-11-19 19:07 UTC | 49 | Assistant Service Operations Engineer; Winter / Spring Intern - Major Gifts; Business Development Executive; Winter / Spring Intern - Social Media; Collections Coordinator | Endpoint nao documentado. Subdominios sem pagina de carreiras respond… |
| BrassRing | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Breezy HR | ATS | VERIFIED | Feed JSON publico da pagina de vagas ({company}.breezy.hr/json), list… | 3/3 | 2026-11-19 19:07 UTC | 45 | BBS Practitioner Global Grade 09; Assistant Director of Care: London, Ontario; 1 Demo/Testing Position - Clone 12/18/2014; Condition Monitor Global Grade 09; Dietary Aide | Endpoint nao documentado; nem todo tenant habilita o feed (404). Sem … |
| CareerPlug | ATS | VERIFIED | HTML server-side `{sub}.careerplug.com/jobs` com JSON-LD ItemList de … | 1/1 | 2026-11-19 19:41 UTC | 16 | Area Coach - Hair Stylist Educator; Area Coach - Hair Stylist Educator; Area Coach - Hair Stylist Educator; Area Coach - Hair Stylist Educator; Area Coach - Hair Stylist Educator | `/jobs.js` e `/j/` sao proibidos pelo robots.txt e nao sao usados.; A… |
| Comeet | ATS | VERIFIED | API publica careers-api 2.0 (`company/{uid}/positions`); o token publ… | 2/2 | 2026-11-19 19:41 UTC | 269 | Account Executive - Austin, TX; Enterprise Account Executive (EMEA & DACH Region); Account Executive - Federal; Technical Account Manager; Account Executive - Federal | Exige a URL da pagina de carreiras da empresa (de onde vem o token pu… |
| CSOD | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Dayforce | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Deel | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Dover | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Eightfold AI | ATS | VERIFIED | Endpoint publico `api/apply/v2/jobs?domain=` das paginas de carreiras… | 1/1 | 2026-11-19 19:42 UTC | 321 | AI Analyst; AI Analyst; AI Business Development Manager - EMEA/LATAM; Associate Inside Sales Rep - Dutch Speaker; Associate Inside Sales Rep - French Speaker | Sem descricao na listagem (so no detalhe por vaga).; Tenants com a AP… |
| FirstStage | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Freshteam | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Gem.com | ATS | VERIFIED | Job Board API publica (api.gem.com/job_board/v0/{board_slug}/job_post… | 2/2 | 2026-11-19 19:08 UTC | 83 | Administrative Coordinator, EPD; Engineering Manager (AI Products); Baremetal Infrastructure Engineer; BizOps – GTM & Monetization; Business Development Lead - Air Force | Slug inexistente responde 404. Sem campo de salario (so no texto da d… |
| GoHire | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Greenhouse | ATS | VERIFIED | API JSON publica do Job Board (boards-api.greenhouse.io/v1/boards/{bo… | 4/4 | 2026-11-19 19:12 UTC | 1248 | Account Executive, Mobile Gaming & Apps; Abuse Investigator; Account Executive (Hotels); AI Transformation Owner, CRO; Associate Commercial Counsel | Apenas boards publicos com board_token conhecido; salario so quando h… |
| HiBob | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| HireBridge | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| HireHive | ATS | VERIFIED | Jobs API v2 publica ({subdomain}.hirehive.com/api/v2/jobs), paginada … | 3/3 | 2026-11-19 19:15 UTC | 78 | (Senior) Pre Sales Consultant – EHS & ESG (m/f/d) - Dutch s…; Account Executive; Applications Functional Support Analyst - MS Dynamics 365 F…; Account Executive; Accounts Payable Administrator | Termos de uso do HireHive nao revisados. Salario so quando preenchido… |
| Hireology | ATS | VERIFIED | HTML renderizado no servidor de careers.hireology.com/{slug}?page=N (… | 3/3 | 2026-11-19 19:43 UTC | 205 | Academic Coach (Anderson, SC); (CNA) Day an Evening shift; Content Marketing Specialist; Academic Tutor; Administrator | Sem API JSON publica; depende de seletores data-qa-id (frageis).; Lis… |
| HiringThing | ATS | VERIFIED | Feed RSS publico {subdomain}.hiringthing.com/api/rss.xml com descrica… | 3/3 | 2026-11-19 19:43 UTC | 76 | Builder/Carpenter \| Exhibits, Events \| Clinton, CT; Assistant Community Manager; Chief Operating Officer / Integrator [HT-1051010]; Builder/Carpenter \| Exhibits, Events \| Raleigh Durham, NC; Assistant Service Manager | Feed sem data de publicacao nem salario.; Se o feed limitar itens, co… |
| iCIMS | ATS | VERIFIED | HTML server-side do portal `{host}/jobs/search?ss=1&in_iframe=1&pr=N`. | 2/2 | 2026-11-19 19:42 UTC | 504 | Academic Department Administrator, Art History - Emory Coll…; Activities Coordinator, Sub Acute Program; Academic Research Scientist- School of Medicine, Pulmonary; Advanced Practice Provider, Neurocritical Care, Community H…; Academic Research Scientist - School of Medicine, Surgery | Portais customizados que nao usam o layout iCIMS_JobCardItem nao sao … |
| iSolvedHire | ATS | VERIFIED | Sitemap /job_site_map.xml + JSON-LD JobPosting em /jobs/{id}.html (bu… | 3/3 | 2026-11-19 19:43 UTC | 111 | AI/ML Engineer (Senior); (Offshore) Principal AI Engineer; 1st Shift Production Positions- Kingston, PA; Azure Engineer; Accounts Receivable Analyst | Uma requisicao por vaga; maximo de 200 vagas por coleta (excedente ->… |
| JazzHR | ATS | VERIFIED | HTML server-side `{sub}.applytojob.com/apply` (li.list-group-item). | 2/2 | 2026-11-19 19:42 UTC | 25 | Acquisition Program Manager - Full Performance; Customer Success Manager; Acquisition Program Manager - Senior/Expert; i3 Insider Risk Analyst – Australia Public Sector - Canberra; Budget Analyst- Senior | Subdominio inexistente redireciona ao site da JazzHR e vira ParseErro… |
| Jobvite | ATS | VERIFIED | HTML server-side `jobs.jobvite.com/{company}/jobs` (table.jv-job-list… | 2/2 | 2026-11-19 19:42 UTC | 87 | Commercial Strategy Manager/Senior Analyst; AI Integration Engineer; Manager, Industry Solutions; Buyer; Market Development Analyst | Paginas com layout proprio que carregam a lista por JavaScript (ex.: … |
| JOIN.com | ATS | VERIFIED | `__NEXT_DATA__` da pagina publica `join.com/companies/{company}` (pag… | 1/2 | 2026-11-19 19:42 UTC | 4 | Compliance and Quality Coordinator (FTC); Digitaler Bestandskundenbetreuer (Outbound) (m/w/d); Sales Consultant (m/w/d); Sales Consultant (m/w/d) | Paginacao `?page=N` nao verificada (nenhum quadro com mais de 5 vagas… |
| Kula.ai | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Lever.co | ATS | VERIFIED | Postings API publica (api.lever.co/v0/postings/{company}?mode=json; t… | 3/3 | 2026-11-19 19:07 UTC | 405 | Administrative Business Partner; API Engineer; Account Executive - Commercial, Spotify Technology; Administrative Business Partner; BI Developer | A regiao (global\|eu) de cada tenant precisa ser conhecida; slug inex… |
| Manatal | ATS | VERIFIED | API da pagina publica careers-page.com (api.manatal.com/open/v3/caree… | 3/3 | 2026-11-19 19:08 UTC | 57 | Optometrist; BUYER/TRADER; AI Content Marketing Internship; Parts Salesman; Certified Nurse Midwife - Full time | Endpoint observado no site publico, nao documentado (a API oficial ex… |
| Oracle Recruiting | ATS | VERIFIED | REST publico `hcmRestApi/.../recruitingCEJobRequisitions` (finder fin… | 1/2 | 2026-11-19 19:42 UTC | 361 | Admin Support Apprenticeships – Initial Assessment - Fareha…; Advance Associate, Customer Service; Advance Associate, Customer Service Contingent Worker; Advance Specialist - Project Management; Advanced Associate Professional Services (TN, MI, IL, IN, O… | Tenants atras de WAF (ex.: jpmc) respondem 403 e nao sao suportados.;… |
| PageUp | ATS | VERIFIED | Feed RSS publico careers.pageuppeople.com/{client_id}/cw/{lang}/rss c… | 3/3 | 2026-11-19 19:43 UTC | 66 | Accommodation Maintenance Operative; Accounting Intern; Care Partner; Floating Support Worker; Art History Adjunct Instructor | Identificador do cliente e numerico e nao descobrivel pelo nome da em… |
| Paradox | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Paycom | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Paycor | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Paylocity | ATS | VERIFIED | Pagina publica `recruiting.paylocity.com/recruiting/jobs/All/{guid}` … | 2/2 | 2026-11-19 19:42 UTC | 698 | 2nd Shift Production Manager \| $51K-$60K; BEACON SOLUTIONS ANALYST MAINTENANCE; 4-Day Week \| No Call \| Primary Care Physician; BUSINESS ANALYST; Acute Rehab RN $63K–$86K Full Time Days Nights Ottumwa IA $… | Descricao da listagem e trecho truncado: fica None.; Vagas internas (… |
| Personio | ATS | VERIFIED | Feed XML publico {account}.jobs.personio.{com\|de}/xml (workzag-jobs/… | 2/2 | 2026-11-19 19:43 UTC | 9 | Electronics Repair Specialist; Business Development Representative (d/f/m); Future Talent — Apply Here!; Test Job; Group Accounting Manager (all genders) | Endpoint de feed XML nao documentado como API; o dominio (.com/.de) p… |
| Phenom People | ATS | VERIFIED | POST https://{domain}/widgets (ddoKey refineSearch, from/size), JSON … | 2/2 | 2026-11-19 19:43 UTC | 178 | Default High Risk Specialist; Accounts Payable Manager; Full Stack AI Software Engineer; ACH Manager, Enterprise Products - onsite, Columbus - ACH P…; Full Stack AI Software Engineer | Endpoint nao documentado, um dominio por cliente; pageId pode variar … |
| Pinpoint | ATS | VERIFIED | Feed publico {subdomain}.pinpointhq.com/postings.json, lista completa… | 2/2 | 2026-11-19 19:08 UTC | 190 | After-sales Key Account Executive, Cotonou (Benin Republic); Senior Product Marketing Manager; After-Sales Manager - Cotonou (Benin Republic); Senior Technical Success Engineer; After Sales - Manager, Togo | Sem data de publicacao (so deadline_at). Remuneracao so quando compen… |
| Polymer | ATS | VERIFIED | API usada pela pagina jobs.polymer.co (api.polymer.co/v1/hire/organiz… | 3/3 | 2026-11-19 19:08 UTC | 32 | Contractor Sales Lead (Colorado); Account Executive, AI Compute; Accounts Payable Assistant (Entry Level); Contractor Sales Lead (Massachusetts); Business Operations Associate | API nao documentada. Sem descricao na listagem (so na pagina da vaga)… |
| Recooty | ATS | VERIFIED | HTML de jobs.recooty.com/{company}?page=N (cartoes h3 a) + JSON-LD Jo… | 1/1 | 2026-11-19 19:45 UTC | 12 | Communications Associate (Entry Level/New Grad); Content Writer; Ecosystem Development Associate; Growth Associate (Entry Level/New Grad); GTM Partnership Specialist | Sem API; seletores de cartao e classes de icone frageis.; Maximo de 2… |
| Recruitee | ATS | VERIFIED | Careers Site API publica ({company}.recruitee.com/api/offers/), lista… | 3/3 | 2026-11-19 19:07 UTC | 33 | Head of Growth & Product; Academic Counselor for MiaPrep Online High School (MOHS); (Senior) Product Owner; Learning Experience Designer; Administrative Support Associate for MiaPrep Online Academy… | Subdominio inexistente responde 404; quadro vazio devolve offers=[]. |
| Rippling | ATS | VERIFIED | API publica do quadro ats.rippling.com (api.rippling.com/platform/api… | 3/3 | 2026-11-19 19:08 UTC | 51 | Call List; Product Delivery Manager; Bilingual Japanese Analyst, Merchant Monitoring; Chief Platform Architect; Senior Product Manager, AI Sales Platform | API nao documentada. Listagem so com nome, departamento, URL e local:… |
| Rival | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| SAP SuccessFactors | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| SmartRecruiters | ATS | BLOCKED | Posting API (api.smartrecruiters.com/v1/companies/{id}/postings?limit… | 0/0 | nunca | - | - | BLOCKED por robots.txt: https://api.smartrecruiters.com/robots.txt de… |
| talentReef | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Taleo | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Teamtailor | ATS | VERIFIED | Feed RSS publico {company}.teamtailor.com/jobs.rss com namespace tt (… | 3/3 | 2026-11-19 19:43 UTC | 51 | Circularity Operations Specialist (gn); Account Executive - Bogota; Advanced Full Stack Software Engineer; Join our Talent Community - Circularity Department; Account Executive - UK Enterprise | Robots/Content-Signal do host declara ai-train=no: usar so para index… |
| Trakstar | ATS | VERIFIED | Sitemap {company}.hire.trakstar.com/sitemap.xml + JSON-LD JobPosting … | 2/2 | 2026-11-19 19:45 UTC | 483 | Administrative Assistant; ​​Electrical Design Engineer ( MEP) - Deerfield Beach, FL; Assistant Project Manager; 1:1 Nurse (SY 24-25) - Nashua, NH; Assistant Project Manager | Uma requisicao por vaga; maximo de 500 vagas por coleta (excedente ->… |
| TriNet | ATS | VERIFIED | HTML do portal app.trinethire.com/companies/{id}-{slug}/jobs + detalh… | 2/2 | 2026-11-19 19:45 UTC | 15 | Clean Beauty Lead \| Abbot Kinney; Brand Ambassador – Dealer Experience; Clean Beauty Lead \| Chicago Market; Communications Operations Specialist; Senior Director, E-Commerce | robots.txt declara Content-Signal ai-train=no (e bloqueia crawlers de… |
| UltiPro | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| WeRecruit | ATS | NOT_STARTED | - | 0/0 | nunca | - | - | - |
| Workable | ATS | VERIFIED | Endpoint publico v3 da pagina de vagas (POST apply.workable.com/api/v… | 3/3 | 2026-11-19 19:07 UTC | 224 | Regional Marketing Manager, Northern Europe; Head of Finance; Agentic AI Architect; Nurse Practitioner GLP-1 Prescriber (1099); AI and Integration Architect | Endpoint nao documentado (usado pela SPA). A listagem nao traz descri… |
| Workday | ATS | TESTED | Endpoint CXS publico da pagina de carreiras (POST {tenant}.{host}.myw… | 0/2 | nunca (ultima tentativa falhou: listagem incompleta) | - | - | Endpoint interno nao documentado; tenant, host (wdN) e site vem da UR… |
| Zoho Recruit | ATS | VERIFIED | Pagina {company}.zohorecruit.com/jobs/Careers com lista completa em J… | 3/3 | 2026-11-19 19:43 UTC | 78 | Azure DevOps Engineer; Administrative Role – Office Clerk; Application Development Lead; CLM Functional Consultant; Agro-meteorologist | Alguns tenants (ex.: rmsi) nao enviam Job_Description na lista.; So o… |
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
