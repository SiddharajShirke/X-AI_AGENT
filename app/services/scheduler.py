from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import Settings
from app.models import SchedulerTickResult
from app.repository import Repository
from app.services.pipeline import Pipeline

logger = logging.getLogger(__name__)


class SchedulerService:
    """Small single-process scheduler suitable for a visible prototype."""

    def __init__(self, settings: Settings, repository: Repository, pipeline: Pipeline):
        self.settings = settings
        self.repository = repository
        self.pipeline = pipeline
        self._stop_event = asyncio.Event()

    def tick(self, now: datetime | None = None) -> SchedulerTickResult:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)

        result = SchedulerTickResult()
        for account in self.repository.list_accounts(enabled_only=True):
            try:
                zone = ZoneInfo(account.timezone)
            except ZoneInfoNotFoundError:
                zone = ZoneInfo("UTC")
            local_now = current.astimezone(zone)
            local_date = local_now.date().isoformat()
            local_time = local_now.strftime("%H:%M")

            for schedule in self.repository.list_schedules(account.id):
                if not schedule.enabled:
                    continue
                if schedule.time_local != local_time:
                    continue
                if schedule.last_run_date == local_date:
                    continue
                try:
                    draft = self.pipeline.generate_draft(
                        account.id, schedule_id=schedule.id
                    )
                    self.repository.mark_schedule_run(
                        account.id, schedule.id, local_date
                    )
                    result.generated.append(draft)
                except Exception as exc:
                    logger.exception(
                        "Scheduled draft generation failed for account %s slot %s",
                        account.id,
                        schedule.id,
                    )
                    result.errors.append(
                        f"@{account.handle} slot {schedule.slot_number}: {exc}"
                    )

            try:
                result.expired_replacements.extend(
                    self.pipeline.expire_and_regenerate(
                        current.astimezone(timezone.utc), x_account_id=account.id
                    )
                )
            except Exception as exc:
                logger.exception(
                    "Approval-timeout processing failed for account %s", account.id
                )
                result.errors.append(f"@{account.handle} timeout processing: {exc}")
        return result

    async def run_forever(self) -> None:
        self._stop_event.clear()
        while not self._stop_event.is_set():
            await asyncio.to_thread(self.tick)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.settings.scheduler_poll_seconds
                )
            except TimeoutError:
                continue

    def stop(self) -> None:
        self._stop_event.set()
