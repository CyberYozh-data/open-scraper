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
from src.settings import settings, setup_logging

# This process never configured logging at all, so its ENTIRE container log was
# in taskiq's own format — a different bracket grammar, no tag, no logger name,
# and untouched by LOG_FORMAT. One of the three services was silently outside
# the logging contract the other two keep. There is no WORKER_STARTUP hook here
# (that event belongs to the worker), so it is configured at import, which is
# when `taskiq scheduler src.queue.scheduler:scheduler` loads this module.
setup_logging(settings.log_level, tag="S")

scheduler = TaskiqScheduler(broker, sources=[LabelScheduleSource(broker)])
