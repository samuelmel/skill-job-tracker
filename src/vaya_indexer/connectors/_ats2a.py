"""Utilitarios dos conectores do lote 2A (download de HTML/XML e padroes de config)."""

from typing import Any

from vaya_indexer.extraction.http import HttpClient

# Hostname puro (sem esquema, porta nem caminho): impede que a config altere o destino.
HOST_PATTERN = r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$"
# Caminho de portal com segmentos seguros, ex.: "en_US/main".
PORTAL_PATH_PATTERN = r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$"


async def get_text(http: HttpClient, url: str, **kwargs: Any) -> str:
    """GET pelo HttpClient (retry/rate limit/UA) devolvendo o corpo como texto."""
    return (await http.request("GET", url, **kwargs)).text
