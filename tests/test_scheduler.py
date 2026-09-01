from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.scheduler import SchedulerService


def test_scheduler_runs_due_slot_only_once_per_local_day(settings, repository, pipeline):
    profile = repository.update_profile({"timezone": "Asia/Kolkata"})
    schedule = repository.list_schedules()[0]
    repository.update_schedule(schedule.id, {"time_local": "09:15", "enabled": True})
    scheduler = SchedulerService(settings, repository, pipeline)
    now = datetime(2026, 8, 10, 9, 15, tzinfo=ZoneInfo(profile.timezone))

    first = scheduler.tick(now)
    second = scheduler.tick(now)

    assert len(first.generated) == 1
    assert len(second.generated) == 0
    assert repository.get_schedule(schedule.id).last_run_date == "2026-08-10"
