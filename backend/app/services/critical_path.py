"""Critical Path Method.

Forward+backward pass over the activity DAG to flag the critical chain. Uses
networkx for DAG validation. Activities with zero total float are critical.
"""

from __future__ import annotations

from datetime import timedelta
from dataclasses import dataclass

import networkx as nx
from sqlalchemy.orm import Session

from app.models.schedule import ScheduleActivity, CriticalPathSnapshot


@dataclass
class CPMResult:
    project_finish: any  # datetime
    critical_ids: list[str]
    total_float_days: float


def recompute(db: Session, project_id: int, *, trigger: str = "manual") -> CriticalPathSnapshot:
    activities = (
        db.query(ScheduleActivity)
        .filter(ScheduleActivity.project_id == project_id)
        .all()
    )
    if not activities:
        raise LookupError(f"no activities for project {project_id}")

    by_id = {a.activity_id: a for a in activities}
    g: nx.DiGraph = nx.DiGraph()
    for a in activities:
        g.add_node(a.activity_id, duration=float(a.duration_days or 0))
    for a in activities:
        for pred in (a.predecessors or []):
            if pred in by_id:
                g.add_edge(pred, a.activity_id)

    if not nx.is_directed_acyclic_graph(g):
        raise ValueError("schedule contains a cycle")

    # Earliest start / finish via topological forward pass.
    es: dict[str, float] = {}
    ef: dict[str, float] = {}
    for n in nx.topological_sort(g):
        preds_ef = [ef[p] for p in g.predecessors(n)]
        es[n] = max(preds_ef, default=0.0)
        ef[n] = es[n] + g.nodes[n]["duration"]

    project_duration = max(ef.values(), default=0.0)

    # Latest finish / start via reverse pass.
    lf: dict[str, float] = {n: project_duration for n in g.nodes}
    ls: dict[str, float] = {}
    for n in reversed(list(nx.topological_sort(g))):
        succs_ls = [ls[s] for s in g.successors(n)]
        lf[n] = min(succs_ls) if succs_ls else project_duration
        ls[n] = lf[n] - g.nodes[n]["duration"]

    total_float = {n: ls[n] - es[n] for n in g.nodes}
    critical_ids = [n for n, f in total_float.items() if f <= 1e-6]

    for a in activities:
        a.is_critical = a.activity_id in set(critical_ids)

    base_start = min((a.planned_start for a in activities), default=None)
    project_finish = (
        base_start + timedelta(days=project_duration) if base_start else None
    )

    snap = CriticalPathSnapshot(
        project_id=project_id,
        critical_activity_ids=critical_ids,
        project_finish=project_finish,
        total_float_days=min(total_float.values(), default=0.0),
        trigger=trigger,
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)

    # Silo-buster: every CPM recompute pings the notification bus. The bus
    # is dedup'd internally so a stable schedule won't spam alerts.
    try:
        from app.services import notification_service
        notification_service.evaluate_for_project(
            db, project_id, trigger=f"cpm_recompute:{trigger}",
        )
    except Exception:  # pragma: no cover — notification failures must never
        # block the schedule recompute itself
        pass

    # Change Order Sentinel: a CPM shift can move activities on/off the
    # critical path. Re-classify and fire any new alerts.
    try:
        from app.services import change_order_sentinel
        change_order_sentinel.scan(db, project_id, trigger=f"cpm_recompute:{trigger}")
    except Exception:  # pragma: no cover
        pass

    return snap
