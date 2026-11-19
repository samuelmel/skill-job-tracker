from decimal import Decimal

import pytest

from vaya_indexer.connectors.jsonld import extract_job_postings, job_posting_to_normalized
from vaya_indexer.core.exceptions import ParseError

# HTML SINTETICO (nao e evidencia de cobertura de nenhuma plataforma).
HTML = """
<html><head>
<script type="application/ld+json">{ invalid json</script>
<script type="application/ld+json">
{"@context": "https://schema.org", "@graph": [
  {"@type": "Organization", "name": "Exemplo"},
  {"@type": "JobPosting", "title": "  Engenheira de   Dados ",
   "description": "<p>Pipelines <b>reais</b></p>",
   "datePosted": "2024-05-01", "employmentType": ["FULL_TIME"],
   "hiringOrganization": {"@type": "Organization", "name": "Exemplo Ltda"},
   "jobLocationType": "TELECOMMUTE",
   "jobLocation": [{"@type": "Place", "address": {"@type": "PostalAddress",
       "addressLocality": "Lisboa", "addressCountry": "PT"}}],
   "url": "https://exemplo.test/jobs/1",
   "baseSalary": {"@type": "MonetaryAmount", "currency": "EUR",
       "value": {"@type": "QuantitativeValue", "minValue": 40000, "maxValue": 55000,
                 "unitText": "YEAR"}}}
]}
</script>
<script type="application/ld+json">
[{"@type": ["JobPosting"], "title": "Designer", "baseSalary": {"currency": "USD",
  "value": {"value": 50}}}]
</script>
</head><body></body></html>
"""


def test_extract_ignores_invalid_blocks_and_walks_graph() -> None:
    postings = extract_job_postings(HTML)
    assert [p["title"].strip() for p in postings] == ["Engenheira de   Dados", "Designer"]
    assert extract_job_postings("<html></html>") == []


def test_conversion_to_normalized_job() -> None:
    job = job_posting_to_normalized(extract_job_postings(HTML)[0])
    assert job.title == "Engenheira de Dados"
    assert job.company_name == "Exemplo Ltda"
    assert job.location == "Lisboa, PT"
    assert job.original_url == "https://exemplo.test/jobs/1"
    assert job.original_posted_at is not None and job.original_posted_at.year == 2024
    assert job.employment_type == "FULL_TIME"
    assert job.workplace_type == "REMOTE"
    assert (job.salary_min, job.salary_max, job.salary_currency, job.salary_period) == (
        Decimal(40000),
        Decimal(55000),
        "EUR",
        "YEAR",
    )
    assert job.description is not None and "Pipelines reais" in job.description


def test_salary_without_period_is_not_invented() -> None:
    job = job_posting_to_normalized(extract_job_postings(HTML)[1])
    assert job.title == "Designer"
    assert job.salary_min is None and job.salary_period is None and job.location is None


def test_missing_title_raises() -> None:
    with pytest.raises(ParseError):
        job_posting_to_normalized({"@type": "JobPosting"})
