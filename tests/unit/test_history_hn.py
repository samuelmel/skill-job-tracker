import gzip
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from vaya_indexer.core.exceptions import HttpStatusError
from vaya_indexer.lake import history_hn as hn

NOW = datetime(2026, 11, 23, tzinfo=UTC)


def _hit(oid: int, title: str, author: str = "whoishiring") -> dict[str, Any]:
    return {
        "objectID": str(oid),
        "title": title,
        "author": author,
        "created_at": "2024-03-01T15:00:00Z",
    }


def test_title_filter_and_month_parsing() -> None:
    hits = [
        _hit(1, "Ask HN: Who is hiring? (March 2024)"),
        _hit(2, "Ask HN: Who wants to be hired? (March 2024)"),
        _hit(3, "Ask HN: Freelancer? Seeking freelancer? (March 2024)"),
        _hit(4, "Ask HN: Who Is Hiring? (May 2012) "),
        _hit(5, "Ask HN: Who is hiring right now?"),
        _hit(6, "Ask HN: Who is hiring? (April 2024)", author="someoneelse"),
    ]
    found = hn.select_threads(hits)
    assert [(t.thread_id, t.month) for t in found.threads] == [(4, "2012-05"), (1, "2024-03")]
    assert found.skipped_titles == ["Ask HN: Who is hiring right now?"]
    assert hn.parse_month("Ask HN: Who is hiring? (Smarch 2024)") is None


def test_duplicates_and_missing_months() -> None:
    found = hn.select_threads(
        [
            _hit(1, "Ask HN: Who is hiring? (January 2020)"),
            _hit(2, "Ask HN: Who is hiring? (January 2020)"),
            _hit(3, "Ask HN: Who is hiring? (April 2020)"),
        ]
    )
    assert found.duplicate_months == {"2020-01": [1, 2]}
    assert hn.missing_months(t.month for t in found.threads) == ["2020-02", "2020-03"]


def test_redaction_of_email_and_phone() -> None:
    text = hn.clean_text(
        'Acme | Remote | $120000 - 180000 | 2024-03-01<p>Mail <a href="mailto:jobs@acme.com">'
        "jobs@acme.com</a> or jane.doe+hn@example.co.uk, call +1 (415) 555-0100 "
        "or 415-555-0199 or +55 11 91234-5678. apply at "
        '<a href="https://acme.com/careers/very/long">https://acme.com/careers/ver...</a> '
        "or bob [at] foo [dot] com"
    )
    assert "[email]" in text
    assert "@" not in text
    assert text.count("[phone]") == 3
    assert "$120000 - 180000" in text and "2024-03-01" in text
    assert "https://acme.com/careers/very/long" in text
    assert "\n" in text  # <p> vira quebra de linha


class FakeHttp:
    def __init__(self, algolia: dict[int, Any], firebase: dict[int, Any]) -> None:
        self.algolia = algolia
        self.firebase = firebase
        self.calls: list[str] = []
        self.hits: list[dict[str, Any]] = []

    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append(url)
        if url == hn.ALGOLIA_SEARCH:
            return {"hits": self.hits, "nbPages": 1}
        item_id = int(url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".json"))
        if url.startswith("https://hn.algolia.com"):
            return self.algolia[item_id]
        value = self.firebase[item_id]
        if isinstance(value, Exception):
            raise value
        return value


def _comment(
    cid: int, parent: int, text: str | None, author: str = "secret_user"
) -> dict[str, Any]:
    return {
        "id": cid,
        "parent_id": parent,
        "type": "comment",
        "author": author,
        "text": text,
        "created_at": "2024-03-01T16:00:00.000Z",
        "children": [],
    }


def _world() -> FakeHttp:
    first = {
        **_comment(101, 100, "Acme | Remote<p>mail me: a@b.com"),
        "children": [_comment(110, 101, "reply")],
    }
    tree = {
        "id": 100,
        "children": [
            first,
            _comment(102, 100, "Beta | NYC"),
            _comment(103, 100, None),
            _comment(104, 100, "   "),
        ],
    }
    firebase: dict[int, Any] = {
        100: {"id": 100, "kids": [101, 102, 103, 104, 105, 106]},
        105: {"id": 105, "deleted": True},
        106: {"id": 106, "dead": True, "text": "spam", "time": 1700000000},
    }
    http = FakeHttp({100: tree}, firebase)
    http.hits = [_hit(100, "Ask HN: Who is hiring? (March 2024)")]
    return http


def _assert_no_pii_on_disk(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            blob = path.read_bytes()
            blob = gzip.decompress(blob) if path.suffix == ".gz" else blob
            assert b"secret_user" not in blob and b"a@b.com" not in blob


async def test_top_level_only_deleted_excluded_no_author(tmp_path: Path) -> None:
    http = _world()
    summary = await hn.load_history(http, tmp_path, now=NOW)
    assert summary.loaded == ["2024-03"] and summary.incomplete == {}
    thread_dir = tmp_path / "history/hn/raw/month=2024-03/thread=100"
    with gzip.open(thread_dir / hn.POSTS_FILE, "rt", encoding="utf-8") as fh:
        posts = [json.loads(line) for line in fh.read().splitlines()]
    assert [p["post_id"] for p in posts] == [101, 102]  # sem resposta 110, vazios, deleted, dead
    assert set(posts[0]) == {"post_id", "thread_id", "month", "created_at", "text"}
    assert "[email]" in posts[0]["text"]
    manifest = json.loads((thread_dir / hn.MANIFEST_FILE).read_text(encoding="utf-8"))
    assert manifest["complete"] is True and manifest["post_count"] == 2
    assert manifest["api_top_level_count"] == 4 and manifest["firebase_kids_count"] == 6
    assert manifest["schema_version"] == hn.SCHEMA_VERSION and len(manifest["sha256"]) == 64
    _assert_no_pii_on_disk(tmp_path)


async def test_idempotent_resume_skips_complete_but_refetches_newest(tmp_path: Path) -> None:
    http = _world()
    http.hits.append(_hit(50, "Ask HN: Who is hiring? (February 2024)"))
    http.algolia[50] = {"id": 50, "children": [_comment(51, 50, "Old | Co")]}
    http.firebase[50] = {"id": 50, "kids": [51]}
    first = await hn.load_history(http, tmp_path, now=NOW)
    assert sorted(first.loaded) == ["2024-02", "2024-03"]
    http.calls.clear()
    second = await hn.load_history(http, tmp_path, now=NOW)
    assert second.skipped_complete == ["2024-02"] and second.loaded == ["2024-03"]
    assert hn.ALGOLIA_ITEM.format(id=50) not in http.calls
    assert not list((tmp_path / "history/hn/raw/month=2024-03").glob(".*"))  # sem restos


async def test_incomplete_on_fetch_failure_and_retried(tmp_path: Path) -> None:
    http = _world()
    http.firebase[107] = {"id": 107, "text": "Gamma | Berlin", "time": 1709300000}
    http.firebase[100]["kids"].append(107)
    http.hits.append(_hit(50, "Ask HN: Who is hiring? (February 2024)"))
    http.algolia[50] = {"id": 50, "children": [_comment(51, 50, "Old | Co")]}
    http.firebase[50] = {"id": 50, "kids": [51, 52]}
    http.firebase[52] = HttpStatusError("https://x", 500)
    summary = await hn.load_history(http, tmp_path, now=NOW)
    # post vivo ausente no Algolia mas presente no Firebase e resgatado: mes 03 completo
    assert "2024-03" not in summary.incomplete
    assert "2024-02" in summary.incomplete
    path = tmp_path / "history/hn/raw/month=2024-02/thread=50" / hn.MANIFEST_FILE
    assert json.loads(path.read_text(encoding="utf-8"))["complete"] is False
    # mes incompleto nao e pulado na proxima execucao
    http.firebase[52] = {"id": 52, "deleted": True}
    again = await hn.load_history(http, tmp_path, now=NOW)
    assert "2024-02" in again.loaded and again.incomplete == {}


async def test_algolia_id_missing_in_firebase_is_incomplete(tmp_path: Path) -> None:
    http = _world()
    http.firebase[100]["kids"] = [101, 105, 106]
    summary = await hn.load_history(http, tmp_path, now=NOW)
    assert summary.incomplete == {"2024-03": ["algolia_not_in_firebase_kids:3"]}


async def test_range_filter(tmp_path: Path) -> None:
    http = _world()
    summary = await hn.load_history(http, tmp_path, start="2024-04", now=NOW)
    assert summary.loaded == [] and summary.discovered == ["2024-03"]


def test_recover_restores_interrupted_swap(tmp_path: Path) -> None:
    final = tmp_path / "month=2024-03" / "thread=1"
    old = final.parent / f".old-{final.name}-abc"
    old.mkdir(parents=True)
    (old / "manifest.json").write_text("{}")
    hn._recover(final)
    assert (final / "manifest.json").exists() and not old.exists()


@pytest.mark.parametrize("raw", [None, "", "<p></p>"])
def test_clean_text_empty(raw: str | None) -> None:
    assert hn.clean_text(raw) == ""


def test_redaction_of_truncated_and_odd_emails() -> None:
    for raw in ["me at jfeala@_.com, ok", "mail alex.b@clou... now", "x hello_@_moby.gg y"]:
        assert "@" not in hn.clean_text(raw)
