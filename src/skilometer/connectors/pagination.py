"""Paginacao por offset/pagina com limite de seguranca (`max_pages`)."""

from collections.abc import Awaitable, Callable, Hashable, Sequence
from dataclasses import dataclass, field


@dataclass
class Paginated[T]:
    items: list[T] = field(default_factory=list)
    # False se o limite de paginas foi atingido ou a fonte repetiu itens (cursor ignorado):
    # a listagem pode estar truncada e nao deve fechar vagas ausentes.
    complete: bool = True
    pages: int = 0


async def paginate[T](
    fetch_page: Callable[[int], Awaitable[Sequence[T]]],
    *,
    start: int = 0,
    step: int = 1,
    page_size: int | None = None,
    max_pages: int = 100,
    key: Callable[[T], Hashable] | None = None,
) -> Paginated[T]:
    """Chama `fetch_page(cursor)` com cursor = start, start+step, ... ate esgotar.

    Termina (complete=True) numa pagina vazia ou, se `page_size` e dado, mais curta que ele.
    Com `key`, uma pagina sem nenhum item novo interrompe (complete=False).
    """
    if max_pages < 1 or step < 1:
        raise ValueError("max_pages e step devem ser >= 1")
    result: Paginated[T] = Paginated()
    seen: set[Hashable] = set()
    cursor = start
    while result.pages < max_pages:
        page = list(await fetch_page(cursor))
        result.pages += 1
        if not page:
            return result
        page_len = len(page)
        if key is not None:
            new = [item for item in page if key(item) not in seen]
            seen.update(key(item) for item in page)
            if not new:
                result.complete = False
                return result
            page = new
        result.items.extend(page)
        if page_size is not None and page_len < page_size:
            return result
        cursor += step
    result.complete = False
    return result


async def paginate_offset[T](
    fetch_page: Callable[[int], Awaitable[Sequence[T]]],
    *,
    page_size: int,
    max_pages: int = 100,
    key: Callable[[T], Hashable] | None = None,
) -> Paginated[T]:
    """`fetch_page(offset)` com offset 0, page_size, 2*page_size, ..."""
    return await paginate(
        fetch_page, start=0, step=page_size, page_size=page_size, max_pages=max_pages, key=key
    )


async def paginate_pages[T](
    fetch_page: Callable[[int], Awaitable[Sequence[T]]],
    *,
    first_page: int = 1,
    page_size: int | None = None,
    max_pages: int = 100,
    key: Callable[[T], Hashable] | None = None,
) -> Paginated[T]:
    """`fetch_page(numero)` com numero first_page, first_page+1, ..."""
    return await paginate(
        fetch_page, start=first_page, step=1, page_size=page_size, max_pages=max_pages, key=key
    )
