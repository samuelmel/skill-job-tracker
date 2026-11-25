import uuid

import pytest

from skilometer.processing.dedup import (
    DedupAction,
    JobProfile,
    best_match,
    decide,
    score_match,
    title_similarity,
)
from skilometer.processing.normalization import canonical_fingerprint

COMPANY = uuid.uuid4()
SRC_A, SRC_B = uuid.uuid4(), uuid.uuid4()
WORDS = [f"palavra{i}" for i in range(80)]
DESC = " ".join(WORDS[:60])


def _profile(
    title: str = "Senior Backend Engineer",
    location: str | None = "Berlin, Germany",
    description: str | None = DESC,
    urls: frozenset[str] = frozenset(),
    source: uuid.UUID = SRC_A,
    company: uuid.UUID = COMPANY,
    company_name: str = "Acme",
) -> JobProfile:
    return JobProfile(
        company_id=company,
        title=title,
        location=location,
        description=description,
        urls=urls,
        source_ids=frozenset({source}),
        fingerprint=canonical_fingerprint(company_name, title, location),
    )


def test_canonical_url_match_merges() -> None:
    a = _profile(urls=frozenset({"https://acme.com/jobs/1"}), description=None)
    b = _profile(urls=frozenset({"https://acme.com/jobs/1"}), source=SRC_B, description=None)
    match = score_match(a, b)
    assert match.score == 1.0 and match.method == "canonical_url" and not match.vetoed
    assert decide(match, 0.90, 0.75) is DedupAction.MERGE


def test_fingerprint_plus_description_merges() -> None:
    match = score_match(_profile(), _profile(source=SRC_B))
    assert match.method == "fingerprint+description"
    assert match.score >= 0.90 and not match.vetoed
    assert decide(match, 0.90, 0.75) is DedupAction.MERGE


def test_intermediate_score_goes_to_review() -> None:
    other = " ".join(WORDS[:40] + WORDS[60:80])  # ~metade dos shingles em comum
    match = score_match(_profile(), _profile(source=SRC_B, description=other))
    assert 0.75 <= match.score < 0.90
    assert decide(match, 0.90, 0.75) is DedupAction.REVIEW


def test_location_veto() -> None:
    match = score_match(_profile(), _profile(source=SRC_B, location="Munich, Germany"))
    assert match.vetoed and match.score == 0.0
    assert decide(match, 0.90, 0.75) is DedupAction.NONE


def test_different_companies_never_merge_even_with_same_url() -> None:
    a = _profile(urls=frozenset({"https://x.com/1"}))
    b = _profile(urls=frozenset({"https://x.com/1"}), source=SRC_B, company=uuid.uuid4())
    assert score_match(a, b).vetoed


def test_same_source_never_merges() -> None:
    assert score_match(_profile(), _profile()).vetoed  # mesmo source_id


def test_same_title_with_different_description_does_not_merge() -> None:
    other = " ".join(f"outro{i}" for i in range(60))
    match = score_match(_profile(), _profile(source=SRC_B, description=other))
    assert decide(match, 0.90, 0.75) is DedupAction.NONE


def test_title_alone_is_never_enough() -> None:
    match = score_match(
        _profile(description=None, location=None),
        _profile(source=SRC_B, description=None, location=None),
    )
    assert match.score <= 0.5
    assert decide(match, 0.90, 0.75) is DedupAction.NONE


def test_seniority_level_difference_vetoes() -> None:
    match = score_match(_profile(), _profile(source=SRC_B, title="Junior Backend Engineer"))
    assert match.vetoed


def test_best_match_picks_highest_non_vetoed() -> None:
    good, bad = uuid.uuid4(), uuid.uuid4()
    result = best_match(
        _profile(),
        [(bad, _profile(source=SRC_B, location="Munich, Germany")), (good, _profile(source=SRC_B))],
    )
    assert result is not None and result[0] == good
    assert (
        best_match(_profile(), [(bad, _profile(source=SRC_B, location="Munich, Germany"))]) is None
    )


@pytest.mark.parametrize(("a", "b"), [("Data Engineer", "data  engineer"), ("QA", "QA")])
def test_title_similarity_identical(a: str, b: str) -> None:
    assert title_similarity(a, b) == 1.0


def test_generic_url_with_different_titles_never_merges() -> None:
    generic = frozenset({"https://acme.com/careers"})
    a = _profile(title="Backend Engineer", urls=generic, description=None, location=None)
    b = _profile(
        title="Office Manager", urls=generic, description=None, source=SRC_B, location=None
    )
    match = score_match(a, b)
    assert match.method != "canonical_url"
    assert decide(match, 0.90, 0.75) is DedupAction.NONE


def test_specific_url_with_unrelated_title_does_not_merge() -> None:
    url = frozenset({"https://acme.com/jobs/42"})
    a = _profile(title="Backend Engineer", urls=url)
    b = _profile(title="Office Manager", urls=url, source=SRC_B)
    assert decide(score_match(a, b), 0.90, 0.75) is DedupAction.NONE


def test_is_specific_url() -> None:
    from skilometer.processing.urls import is_specific_url

    assert is_specific_url("https://a.com/jobs/123")
    assert is_specific_url("https://a.com/careers?gh_jid=5")
    assert not is_specific_url("https://a.com")
    assert not is_specific_url("https://a.com/careers")
    assert not is_specific_url("https://a.com/apply")
