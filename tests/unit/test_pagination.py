from collections.abc import Awaitable, Callable

import pytest

from vaya_indexer.connectors.pagination import paginate, paginate_offset, paginate_pages


def _source(total: int, page_size: int) -> tuple[Callable[[int], Awaitable[list[int]]], list[int]]:
    calls: list[int] = []

    async def fetch(offset: int) -> list[int]:
        calls.append(offset)
        return list(range(offset, min(offset + page_size, total)))

    return fetch, calls


async def test_offset_pagination_until_short_page() -> None:
    fetch, calls = _source(25, 10)
    result = await paginate_offset(fetch, page_size=10)
    assert result.items == list(range(25))
    assert result.complete and result.pages == 3
    assert calls == [0, 10, 20]


async def test_offset_pagination_exact_multiple_ends_on_empty_page() -> None:
    fetch, calls = _source(20, 10)
    result = await paginate_offset(fetch, page_size=10)
    assert len(result.items) == 20 and result.complete and calls == [0, 10, 20]


async def test_max_pages_marks_incomplete() -> None:
    fetch, calls = _source(1000, 10)
    result = await paginate_offset(fetch, page_size=10, max_pages=3)
    assert len(result.items) == 30
    assert result.complete is False and result.pages == 3 and calls == [0, 10, 20]


async def test_page_number_pagination_starts_at_first_page() -> None:
    seen: list[int] = []

    async def fetch(page: int) -> list[str]:
        seen.append(page)
        return [f"job{page}"] if page <= 2 else []

    result = await paginate_pages(fetch, first_page=1)
    assert result.items == ["job1", "job2"] and result.complete and seen == [1, 2, 3]


async def test_repeated_page_detected_via_key() -> None:
    async def fetch(_: int) -> list[dict[str, int]]:
        return [{"id": 1}, {"id": 2}]  # fonte ignora o cursor

    result = await paginate(fetch, key=lambda j: j["id"], max_pages=50)
    assert [j["id"] for j in result.items] == [1, 2]
    assert result.complete is False and result.pages == 2


async def test_invalid_arguments() -> None:
    async def fetch(_: int) -> list[int]:
        return []

    with pytest.raises(ValueError):
        await paginate(fetch, max_pages=0)
