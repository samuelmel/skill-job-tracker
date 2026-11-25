from collections.abc import Sequence

from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from skilometer.database.models import ImplementationStatus, Platform
from skilometer.platforms_catalog import PlatformEntry

# Status que dependem de evidencia (conector/execucao): o seed nunca os sobrescreve.
_EVIDENCE_STATUSES = (
    ImplementationStatus.IMPLEMENTED,
    ImplementationStatus.TESTED,
    ImplementationStatus.VERIFIED,
)


class PlatformRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_catalog(self, entries: Sequence[PlatformEntry]) -> None:
        """Upsert por slug. Status IMPLEMENTED/TESTED/VERIFIED (evidencia) nunca e sobrescrito."""
        stmt = insert(Platform).values(
            [
                {
                    "name": e.name,
                    "slug": e.slug,
                    "category": e.category,
                    "connector_type": e.connector_type,
                    "implementation_status": e.implementation_status,
                    "docs_url": e.docs_url,
                    "extraction_strategy": e.extraction_strategy,
                    "notes": e.notes,
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
                "docs_url": stmt.excluded.docs_url,
                "extraction_strategy": stmt.excluded.extraction_strategy,
                "notes": stmt.excluded.notes,
                "implementation_status": case(
                    (
                        Platform.implementation_status.in_(_EVIDENCE_STATUSES),
                        Platform.implementation_status,
                    ),
                    else_=stmt.excluded.implementation_status,
                ),
            },
        )
        await self.session.execute(stmt)

    async def get_by_slug(self, slug: str) -> Platform | None:
        return await self.session.scalar(select(Platform).where(Platform.slug == slug))

    async def get(self, platform_id: object) -> Platform | None:
        return await self.session.get(Platform, platform_id)
