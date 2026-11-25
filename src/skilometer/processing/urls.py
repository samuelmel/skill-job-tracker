"""Normalizacao de URLs para comparacao (identidade forte entre fontes)."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = frozenset({"gh_src", "source", "ref", "fbclid", "gclid", "lever-source"})


def _is_tracking(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith("utm_") or lowered in _TRACKING_PARAMS


def canonical_url(url: str) -> str:
    """Host em minusculas, sem fragmento, sem parametros de tracking e sem barra final.

    Os demais parametros sao mantidos (e ordenados): `gh_jid`, por exemplo, identifica a vaga.
    """
    parts = urlsplit(url.strip())
    query = urlencode(
        sorted(
            (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)
        )
    )
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


_ID_PARAMS = frozenset({"gh_jid", "jobid", "job_id", "jid", "id", "reqid", "req_id", "requisition"})
_GENERIC_SEGMENTS = frozenset(
    "careers career jobs job apply positions openings vacancies join search home index "
    "work-with-us en pt us".split()
)


def is_specific_url(url: str) -> bool:
    """True se a URL (canonica) identifica uma vaga: id na query ou ultimo segmento nao generico."""
    parts = urlsplit(url)
    if any(k.lower() in _ID_PARAMS for k, _ in parse_qsl(parts.query)):
        return True
    segments = [s for s in parts.path.split("/") if s]
    return bool(segments) and segments[-1].lower() not in _GENERIC_SEGMENTS
