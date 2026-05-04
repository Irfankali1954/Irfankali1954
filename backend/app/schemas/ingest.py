from pydantic import BaseModel


class IngestHealthReportOut(BaseModel):
    source: str
    accepted_records: int
    rejected_records: int
    field_coverage: dict[str, float]
    missing_required: list[str]
    accuracy_degradation_pct: float
    health_score: float
    grade: str
    notes: list[str]
