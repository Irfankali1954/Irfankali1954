"""Primavera P6 .XER ingestion stub.

XER is a tab-delimited multi-table dump. A full implementation walks TASK,
TASKPRED, PROJWBS and produces ``ScheduleActivity`` rows. This stub preserves
the contract; replace :func:`parse_xer` with the production parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ParsedActivity:
    activity_id: str
    name: str
    wbs: str
    planned_start: datetime
    planned_finish: datetime
    duration_days: float
    predecessors: list[str]
    successors: list[str]


def parse_xer(content: bytes) -> list[ParsedActivity]:
    """Parse a .XER file into :class:`ParsedActivity` rows.

    TODO: implement full TASK/TASKPRED reader. Stub returns [].
    """
    return []
