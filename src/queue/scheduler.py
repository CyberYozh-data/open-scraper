"""taskiq scheduler entrypoint.

Run exactly ONE instance:
    taskiq scheduler src.queue.scheduler:scheduler src.queue.tasks

The trailing `src.queue.tasks` import makes the scheduled tasks (their
`schedule` labels) discoverable. The scheduler only ENQUEUES periodic tasks —
workers execute them — so running more than one scheduler would enqueue each
periodic task once per instance.
"""
from __future__ import annotations

from taskiq import TaskiqScheduler
from taskiq.schedule_sources import LabelScheduleSource

from src.queue.broker import broker

scheduler = TaskiqScheduler(broker, sources=[LabelScheduleSource(broker)])
