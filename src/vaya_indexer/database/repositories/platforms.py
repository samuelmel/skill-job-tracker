from collections.abc import Sequence

from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from vaya_indexer.database.models import ImplementationStatus, Platform
from vaya_indexer.platforms_catalog import PlatformEntry


class PlatformRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_catalog(self, entries: Sequence[PlatformEntry]) -> None:
        """Upsert por slug. Nunca rebaixa nem sobrescreve um status ja evoluido."""
        stmt = insert(Platform).values(
            [
                {
                    "name": e.name,
                    "slug": e.slug,
                    "category": e.category,
                    "connector_type": e.connector_type,
                    "implementation_status": e.implementation_status,
                }
                for e in entries
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Platform.slug],
            set_={
                "name": stmt.excluded.name,
                "category": stmt.excluded.category,
                "connector_type": stmt.excluded.connector_type,
                "implementation_status": case(
                    (
                        Platform.implementation_status == ImplementationStatus.NOT_STARTED,
                        stmt.excluded.implementation_status,
                    ),
                    else_=Platform.implementation_status,
                ),
            },
        )
        await self.session.execute(stmt)

    async def get_by_slug(self, slug: str) -> Platform | None:
        return await self.session.scalar(select(Platform).where(Platform.slug == slug))

    async def get(self, platform_id: object) -> Platform | None:
        return await self.session.get(Platform, platform_id)
