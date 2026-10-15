from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report import Report


class ReportRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        *,
        report_type: str,
        requested_by_user_id: str | None,
        requested_by_email: str,
        monitored_database_id: str | None,
        database_name_snapshot: str | None,
        incident_id: str | None,
        incident_title_snapshot: str | None,
        period_start: datetime | None,
        period_end: datetime | None,
        timezone: str,
        data_available: bool,
        data_completeness_note: str,
        limitations: list[str],
        content: dict,
    ) -> Report:
        report = Report(
            report_type=report_type,
            requested_by_user_id=requested_by_user_id,
            requested_by_email=requested_by_email,
            monitored_database_id=monitored_database_id,
            database_name_snapshot=database_name_snapshot,
            incident_id=incident_id,
            incident_title_snapshot=incident_title_snapshot,
            period_start=period_start,
            period_end=period_end,
            timezone=timezone,
            data_available=data_available,
            data_completeness_note=data_completeness_note,
            limitations=limitations,
            content=content,
        )
        self.db.add(report)
        await self.db.flush()
        return report

    async def get(self, report_id: str) -> Report | None:
        return await self.db.get(Report, report_id)

    async def list_all(
        self,
        *,
        report_type: str | None = None,
        monitored_database_id: str | None = None,
        incident_id: str | None = None,
        requested_by_user_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Report]:
        query = select(Report).order_by(Report.generated_at.desc()).limit(limit).offset(offset)
        if report_type:
            query = query.where(Report.report_type == report_type)
        if monitored_database_id:
            query = query.where(Report.monitored_database_id == monitored_database_id)
        if incident_id:
            query = query.where(Report.incident_id == incident_id)
        if requested_by_user_id:
            query = query.where(Report.requested_by_user_id == requested_by_user_id)
        result = await self.db.execute(query)
        return list(result.scalars().all())
