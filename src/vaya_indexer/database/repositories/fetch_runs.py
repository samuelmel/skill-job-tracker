import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from vaya_indexer.database.models import ConnectorError, FetchRun, FetchRunStatus


class FetchRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def start(self, source_id: uuid.UUID) -> FetchRun:
        run = FetchRun(
            source_id=source_id, started_at=datetime.now(UTC), status=FetchRunStatus.RUNNING
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def get(self, run_id: uuid.UUID) -> FetchRun | None:
        return await self.session.get(FetchRun, run_id)

    def add_error(
        self,
        run_id: uuid.UUID,
        *,
        external_job_id: str | None,
        error_type: str,
        message: str,
        payload: dict[str, object] | None,
    ) -> None:
        self.session.add(
            ConnectorError(
                fetch_run_id=run_id,
                external_job_id=external_job_id,
                error_type=error_type,
                message=message,
                payload=payload,
            )
        )
