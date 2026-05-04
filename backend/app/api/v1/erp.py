"""ERP / Gantt bridge endpoints.

* POST /erp/{vendor}/sync — bi-directional pull/push for Oracle or SAP
* POST /erp/p6/import     — XER upload
* POST /erp/msp/import    — MPP upload
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_permission
from app.core.rbac import Permission
from app.models.schedule import ScheduleActivity
from app.schemas.ingest import IngestHealthReportOut
from app.schemas.schedule import ScheduleImportResult
from app.services import erp_bridge, ingest_validation, mpp_parser, xer_parser
from app.services import critical_path

router = APIRouter()


@router.post(
    "/{vendor}/sync",
    summary="Bi-directional ERP sync (Oracle Fusion or SAP S/4HANA)",
    description=(
        "Pulls commitments and pushes accruals against the named vendor. "
        "The response includes an ``ingest_health`` block describing which "
        "expected fields were present in the inbound payload — internal EPC "
        "dev teams should aim for grade A by populating optional fields "
        "such as ``permit_due_date`` and ``expected_delivery``."
    ),
    dependencies=[Depends(require_permission(Permission.ERP_SYNC))],
)
def erp_sync(vendor: str, project_code: str) -> dict:
    try:
        result = erp_bridge.sync_project(vendor, project_code)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))

    # The bridge stub returns no records yet; in production this would be the
    # connector's pulled commitments. We score whatever was returned so the
    # ingest contract is exercised end-to-end.
    pulled = result.get("pulled_records") or []
    health = ingest_validation.evaluate_erp_commitments(pulled, vendor=vendor)
    result["ingest_health"] = IngestHealthReportOut(
        **{**health.__dict__, "grade": health.grade}
    ).model_dump()
    return result


def _persist_activities(db: Session, project_id: int, parsed, source: str) -> int:
    count = 0
    for p in parsed:
        db.add(ScheduleActivity(
            project_id=project_id,
            activity_id=p.activity_id,
            name=p.name,
            wbs=p.wbs,
            planned_start=p.planned_start,
            planned_finish=p.planned_finish,
            duration_days=p.duration_days,
            predecessors=p.predecessors,
            successors=p.successors,
            source=source,
        ))
        count += 1
    db.commit()
    return count


@router.post(
    "/p6/import",
    response_model=ScheduleImportResult,
    dependencies=[Depends(require_permission(Permission.SCHEDULE_IMPORT))],
)
async def import_xer(
    project_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(db_session),
) -> ScheduleImportResult:
    parsed = xer_parser.parse_xer(await file.read())
    n = _persist_activities(db, project_id, parsed, source="p6")
    cpm_ok = False
    if n:
        try:
            critical_path.recompute(db, project_id, trigger="xer_import")
            cpm_ok = True
        except Exception:  # pragma: no cover
            cpm_ok = False
    return ScheduleImportResult(
        source="p6_xer",
        activities_ingested=n,
        project_id=project_id,
        cpm_recomputed=cpm_ok,
    )


@router.post(
    "/msp/import",
    response_model=ScheduleImportResult,
    dependencies=[Depends(require_permission(Permission.SCHEDULE_IMPORT))],
)
async def import_mpp(
    project_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(db_session),
) -> ScheduleImportResult:
    parsed = mpp_parser.parse_mpp(await file.read())
    n = _persist_activities(db, project_id, parsed, source="msp")
    cpm_ok = False
    if n:
        try:
            critical_path.recompute(db, project_id, trigger="mpp_import")
            cpm_ok = True
        except Exception:  # pragma: no cover
            cpm_ok = False
    return ScheduleImportResult(
        source="msp_mpp",
        activities_ingested=n,
        project_id=project_id,
        cpm_recomputed=cpm_ok,
    )
