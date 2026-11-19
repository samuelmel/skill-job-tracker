"""Deduplicacao em camadas (funcoes puras, sem I/O).

Camadas de evidencia: URL canonica (forte) > fingerprint (empresa+titulo+local) > similaridade de
titulo > similaridade de descricao; localizacao conhecida e divergente e VETO. Regras duras:
nunca unir empresas diferentes, nunca unir vagas da mesma fonte e nunca decidir so pelo titulo.
"""

import enum
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from vaya_indexer.processing.location import location_relation, normalize_location
from vaya_indexer.processing.normalization import normalize_title
from vaya_indexer.processing.text import html_to_text, jaccard, shingles, word_tokens
from vaya_indexer.processing.urls import is_specific_url

MIN_TITLE_SIMILARITY = 0.6
MIN_DESCRIPTION_WORDS = 20
NO_DESCRIPTION_CAP = 0.85  # sem descricao, nunca passa do limiar de revisao tipico (<0.90)
_LEVEL_TOKENS = frozenset(
    "i ii iii iv v jr junior sr senior lead staff principal intern internship associate "
    "head director manager vp".split()
)
_LOCATION_SCORE = {"same": 1.0, "compatible": 0.8, "unknown": 0.5}


class DedupAction(enum.StrEnum):
    MERGE = "MERGE"
    REVIEW = "REVIEW"
    NONE = "NONE"


@dataclass(frozen=True)
class JobProfile:
    """Dados minimos de uma vaga para comparacao. `urls` ja canonicalizadas."""

    company_id: uuid.UUID
    title: str
    location: str | None
    description: str | None
    urls: frozenset[str]
    source_ids: frozenset[uuid.UUID]
    fingerprint: str


@dataclass(frozen=True)
class MatchScore:
    score: float
    method: str
    reasons: tuple[str, ...]
    vetoed: bool


def _veto(reason: str) -> MatchScore:
    return MatchScore(0.0, "vetoed", (reason,), True)


def title_similarity(a: str, b: str) -> float:
    """Media de similaridade de sequencia (difflib) e Jaccard de tokens, em [0, 1]."""
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    return 0.5 * ratio + 0.5 * jaccard(set(na.split()), set(nb.split()))


def _level_tokens(title: str) -> frozenset[str]:
    return frozenset(t for t in re.split(r"\s+", normalize_title(title)) if t in _LEVEL_TOKENS)


def description_similarity(a: str | None, b: str | None) -> float | None:
    """Jaccard de shingles (3 palavras) do texto das descricoes; None se faltar texto util."""
    ta, tb = word_tokens(html_to_text(a)), word_tokens(html_to_text(b))
    if len(ta) < MIN_DESCRIPTION_WORDS or len(tb) < MIN_DESCRIPTION_WORDS:
        return None
    return jaccard(shingles(ta), shingles(tb))


def _hard_vetoes(new: JobProfile, other: JobProfile) -> MatchScore | None:
    if new.company_id != other.company_id:
        return _veto("empresas diferentes")
    if new.source_ids & other.source_ids:
        return _veto("mesma fonte: ids distintos na mesma fonte sao vagas distintas")
    return None


def score_match(
    new: JobProfile, other: JobProfile, url_min_title: float = MIN_TITLE_SIMILARITY
) -> MatchScore:
    """Pontua o par (nova vaga, vaga canonica existente).

    URL compartilhada so e evidencia forte se for especifica (id/slug da vaga) e o titulo for
    similar (>= url_min_title) com mesmo nivel; URL generica (/careers, /apply) e ignorada.
    """
    if (veto := _hard_vetoes(new, other)) is not None:
        return veto
    relation = location_relation(
        normalize_location(new.location), normalize_location(other.location)
    )
    if relation == "different":
        return _veto(f"localizacoes diferentes: {new.location!r} x {other.location!r}")
    if _level_tokens(new.title) != _level_tokens(other.title):
        return _veto("nivel/senioridade do titulo difere")
    title = title_similarity(new.title, other.title)
    shared_urls = {u for u in new.urls & other.urls if is_specific_url(u)}
    if shared_urls and title >= url_min_title:
        return MatchScore(
            1.0,
            "canonical_url",
            (f"url canonica especifica igual: {min(shared_urls)}", f"titulo={title:.2f}"),
            False,
        )
    if title < MIN_TITLE_SIMILARITY:
        return MatchScore(0.0, "title_similarity", (f"titulo pouco similar ({title:.2f})",), False)
    fingerprint_equal = new.fingerprint == other.fingerprint
    desc = description_similarity(new.description, other.description)
    reasons = [f"titulo={title:.2f}", f"local={relation}"]
    if fingerprint_equal:
        reasons.append("fingerprint igual")
    loc_score = _LOCATION_SCORE[relation]
    if desc is None:
        score = min(NO_DESCRIPTION_CAP, 0.65 * title + 0.35 * loc_score)
        if relation == "unknown":
            score = min(score, 0.5)
            reasons.append("sem evidencia alem do titulo")
        reasons.append("sem descricao comparavel")
    else:
        score = 0.45 * title + 0.15 * loc_score + 0.40 * desc
        reasons.append(f"descricao={desc:.2f}")
    prefix = "fingerprint" if fingerprint_equal else "title_similarity"
    method = f"{prefix}+description" if desc is not None else prefix
    return MatchScore(round(score, 4), method, tuple(reasons), False)


def best_match(
    new: JobProfile,
    candidates: Sequence[tuple[uuid.UUID, JobProfile]],
    url_min_title: float = MIN_TITLE_SIMILARITY,
) -> tuple[uuid.UUID, MatchScore] | None:
    """Melhor candidato nao vetado (maior score); None se nenhum pontuar acima de zero."""
    best: tuple[uuid.UUID, MatchScore] | None = None
    for job_id, profile in candidates:
        match = score_match(new, profile, url_min_title)
        if match.vetoed or match.score <= 0:
            continue
        if best is None or match.score > best[1].score:
            best = (job_id, match)
    return best


def decide(match: MatchScore, merge_threshold: float, review_threshold: float) -> DedupAction:
    if match.vetoed:
        return DedupAction.NONE
    if match.score >= merge_threshold:
        return DedupAction.MERGE
    if match.score >= review_threshold:
        return DedupAction.REVIEW
    return DedupAction.NONE
