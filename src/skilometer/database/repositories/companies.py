import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from skilometer.database.models import Company
from skilometer.processing.normalization import normalize_company_name, normalize_whitespace


class CompanyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(self, name: str) -> uuid.UUID:
        """Resolve a empresa por normalized_name, criando-a se necessario."""
        clean = normalize_whitespace(name)
        normalized = normalize_company_name(clean)
        stmt = (
            insert(Company)
            .values(name=clean, normalized_name=normalized)
            .on_conflict_do_nothing(index_elements=[Company.normalized_name])
            .returning(Company.id)
        )
        created = await self.session.scalar(stmt)
        if created is not None:
            return created
        existing = await self.session.scalar(
            select(Company.id).where(Company.normalized_name == normalized)
        )
        assert existing is not None
        return existing
