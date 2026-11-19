"""FIXTURES DE TESTE sinteticas no formato da API publica do Greenhouse.

Nao sao dados reais nem evidencia de cobertura: servem apenas para testes
unitarios, mockados e de integracao. A cobertura real vem dos testes `live`.
"""

import copy
from typing import Any

BOARD_TOKEN = "acme"
JOBS_URL = f"https://boards-api.greenhouse.io/v1/boards/{BOARD_TOKEN}/jobs"

_JOBS: list[dict[str, Any]] = [
    {
        "id": 1001,
        "internal_job_id": 5001,
        "title": "  Senior   Backend Engineer ",
        "updated_at": "2024-05-02T10:15:00-04:00",
        "first_published": "2024-04-01T09:00:00-04:00",
        "location": {"name": "Remote - Brazil"},
        "absolute_url": "https://boards.greenhouse.io/acme/jobs/1001",
        "company_name": "Acme Inc.",
        "metadata": None,
        "content": "&lt;p&gt;Build &amp;amp; run services.&lt;/p&gt;",
    },
    {
        "id": 1002,
        "internal_job_id": 5002,
        "title": "Data Analyst II",
        "updated_at": "2024-05-03T08:00:00Z",
        "location": {"name": "Sao Paulo"},
        "absolute_url": "https://boards.greenhouse.io/acme/jobs/1002",
        "company_name": "Acme Inc.",
        "metadata": [{"id": 1, "name": "Team", "value": "Data", "value_type": "single_select"}],
        "content": "&lt;p&gt;Analyse data.&lt;/p&gt;",
    },
    {
        "id": 1003,
        "internal_job_id": 5003,
        "title": "Product Designer",
        "updated_at": "2024-05-04T12:00:00Z",
        "first_published": "2024-03-10T12:00:00Z",
        "location": {"name": "Lisbon"},
        "absolute_url": "https://boards.greenhouse.io/acme/jobs/1003",
        "company_name": "Acme Inc.",
        "metadata": None,
        "content": "",
    },
]


def board_payload() -> dict[str, Any]:
    """Copia profunda da resposta de listagem (3 vagas, meta.total coerente)."""
    return {"jobs": copy.deepcopy(_JOBS), "meta": {"total": len(_JOBS)}}
