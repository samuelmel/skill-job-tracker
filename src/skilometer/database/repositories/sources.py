import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from skilometer.database.models import Source


class SourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, source_id: uuid.UUID) -> Source | None:
        return await self.session.get(Source, source_id)

    async def get_by_external_id(self, platform_id: uuid.UUID, external_id: str) -> Source | None:
        return await self.session.scalar(
            select(Source).where(
                Source.platform_id == platform_id, Source.external_id == external_id
            )
        )

    async def create(
        self,
        *,
        platform_id: uuid.UUID,
        company_id: uuid.UUID | None,
        external_id: str,
        source_url: str | None,
        configuration: dict[str, Any],
    ) -> Source:
        source = Source(
            platform_id=platform_id,
            company_id=company_id,
            external_id=external_id,
            source_url=source_url,
            configuration=configuration,
        )
        self.session.add(source)
        await self.session.flush()
        return source
