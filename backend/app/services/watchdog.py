"""Heatmap dwell-time Watchdog.

A single asyncio task that wakes every ``watchdog_interval_seconds``
(default 1800 = 30 min), iterates every project in the database, and
calls :func:`risk_heatmap.evaluate` with ``fire_alerts=True``.

The evaluator is responsible for its own dedupe (24h cooldown per cell),
so this loop is allowed to be aggressive about cadence without spamming.
A failure inside one project must not stop the loop from servicing the
others — exceptions are caught per-project.

The loop is owned by FastAPI's lifespan (see ``app.main``); shutdown
cancels the task cleanly.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.financial import Project
from app.services import risk_heatmap

log = logging.getLogger(__name__)


async def run_once() -> int:
    """One pass over every project. Returns the number of projects scanned."""
    db = SessionLocal()
    scanned = 0
    try:
        for project in db.query(Project).all():
            try:
                risk_heatmap.evaluate(
                    db, project.id, fire_alerts=True, trigger="watchdog",
                )
                scanned += 1
            except Exception as e:  # pragma: no cover
                log.warning("watchdog: project %s failed: %s", project.id, e)
    finally:
        db.close()
    return scanned


async def loop() -> None:
    settings = get_settings()
    if not settings.watchdog_enabled:
        log.info("watchdog disabled by config")
        return
    interval = max(30, int(settings.watchdog_interval_seconds))
    log.info("watchdog starting (interval=%ss)", interval)
    while True:
        try:
            count = await run_once()
            log.info("watchdog tick: %s project(s) evaluated", count)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover
            log.warning("watchdog tick failed: %s", e)
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            log.info("watchdog stopping")
            return
