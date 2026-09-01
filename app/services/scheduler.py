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
        profile = self.repository.get_profile()
        try:
            zone = ZoneInfo(profile.timezone)
        except ZoneInfoNotFoundError:
            zone = ZoneInfo("UTC")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=zone)
        local_now = current.astimezone(zone)
        local_date = local_now.date().isoformat()
        local_time = local_now.strftime("%H:%M")

        result = SchedulerTickResult()
        for schedule in self.repository.list_schedules():
            if not schedule.enabled:
                continue
            if schedule.time_local != local_time:
                continue
            if schedule.last_run_date == local_date:
                continue
            try:
                draft = self.pipeline.generate_draft(schedule_id=schedule.id)
                self.repository.mark_schedule_run(schedule.id, local_date)
                result.generated.append(draft)
            except Exception as exc:
                logger.exception("Scheduled draft generation failed for slot %s", schedule.id)
                result.errors.append(f"Slot {schedule.slot_number}: {exc}")

        try:
            result.expired_replacements.extend(
                self.pipeline.expire_and_regenerate(current.astimezone(timezone.utc))
            )
        except Exception as exc:
            logger.exception("Approval-timeout processing failed")
            result.errors.append(f"Timeout processing: {exc}")
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
