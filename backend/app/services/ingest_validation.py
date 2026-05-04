"""Ingest health scoring.

Each ingest endpoint runs the inbound payload through the
:class:`IngestValidator` matching its source. The validator returns an
``IngestHealthReport`` describing:

* What was accepted / what was rejected.
* Coverage per *expected* field.
* The estimated *Wrap Risk Score accuracy degradation* if expected fields
  are missing — the carrot that motivates internal EPC dev teams to fill
  them in.

The expectation table below is intentionally small and editable in source —
turn it into per-tenant config once a real customer pushes back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FieldExpectation:
    name: str
    required: bool
    accuracy_weight_pct: float   # how much wrap-risk accuracy degrades if missing
    description: str


@dataclass
class IngestHealthReport:
    source: str
    accepted_records: int
    rejected_records: int
    field_coverage: dict[str, float]      # {field: 0.0..1.0}
    missing_required: list[str]
    accuracy_degradation_pct: float       # 0..100; sum of weights for missing fields
    health_score: float                   # 0..100
    notes: list[str] = field(default_factory=list)

    @property
    def grade(self) -> str:
        s = self.health_score
        if s >= 90: return "A"
        if s >= 75: return "B"
        if s >= 60: return "C"
        if s >= 40: return "D"
        return "F"


# ---------- expectation tables ---------------------------------------------

ERP_COMMITMENT_EXPECTATIONS: list[FieldExpectation] = [
    FieldExpectation("project_code", True, 0.0, "Cross-system project key"),
    FieldExpectation("po_number", True, 0.0, "Purchase order"),
    FieldExpectation("vendor_id", True, 0.0, "Supplier identifier"),
    FieldExpectation("amount", True, 0.0, "Commitment value"),
    FieldExpectation("expected_delivery", False, 8.0,
                     "Promised delivery date — needed for long-lead factor"),
    FieldExpectation("permit_due_date", False, 15.0,
                     "Permit due — needed for permit factor accuracy"),
    FieldExpectation("contingency_class", False, 5.0,
                     "Contingency category — drives margin analytics"),
]

DAILY_LOG_EXPECTATIONS: list[FieldExpectation] = [
    FieldExpectation("project_id", True, 0.0, "Project FK"),
    FieldExpectation("activity_id", True, 0.0, "Gantt activity FK"),
    FieldExpectation("raw_transcript", True, 0.0, "Voice-to-text payload"),
    FieldExpectation("crew_count", False, 6.0,
                     "Field crew count — needed for idle-cost accuracy"),
    FieldExpectation("weather_lost_hours", False, 4.0,
                     "Weather hours — drives schedule factor calibration"),
]


# ---------- evaluator ------------------------------------------------------


def evaluate(
    *,
    source: str,
    records: list[dict[str, Any]],
    expectations: list[FieldExpectation],
) -> IngestHealthReport:
    accepted = 0
    rejected = 0
    coverage_count: dict[str, int] = {e.name: 0 for e in expectations}
    rejection_reasons: list[str] = []

    for r in records:
        missing_required = [e.name for e in expectations if e.required and not r.get(e.name)]
        if missing_required:
            rejected += 1
            rejection_reasons.append(f"missing required: {','.join(missing_required)}")
            continue
        accepted += 1
        for e in expectations:
            if r.get(e.name) is not None:
                coverage_count[e.name] += 1

    n = max(accepted, 1)
    coverage = {name: coverage_count[name] / n for name in coverage_count}
    missing_required = [e.name for e in expectations
                        if e.required and coverage[e.name] < 1.0]

    # Accuracy degradation = sum of weights for non-required fields that are
    # absent or sparsely populated. Linear fall-off in coverage.
    degradation = 0.0
    for e in expectations:
        if e.required:
            continue
        present = coverage.get(e.name, 0.0)
        degradation += e.accuracy_weight_pct * (1.0 - present)
    degradation = min(degradation, 100.0)

    if rejected and not records:
        health = 0.0
    else:
        health = max(0.0, 100.0 - degradation - (rejected / max(len(records), 1)) * 40.0)

    notes: list[str] = []
    for e in expectations:
        if e.required:
            continue
        present = coverage.get(e.name, 0.0)
        if present < 1.0:
            notes.append(
                f"Optional field '{e.name}' present in {present*100:.0f}% of records — "
                f"Wrap Risk Score accuracy degraded by up to {e.accuracy_weight_pct:.0f}%."
            )
    if rejected:
        notes.append(f"{rejected} record(s) rejected: " + "; ".join(rejection_reasons[:3]))

    return IngestHealthReport(
        source=source,
        accepted_records=accepted,
        rejected_records=rejected,
        field_coverage=coverage,
        missing_required=missing_required,
        accuracy_degradation_pct=round(degradation, 1),
        health_score=round(health, 1),
        notes=notes,
    )


def evaluate_erp_commitments(records: list[dict[str, Any]], *, vendor: str) -> IngestHealthReport:
    return evaluate(
        source=f"erp:{vendor}:commitments",
        records=records,
        expectations=ERP_COMMITMENT_EXPECTATIONS,
    )


def evaluate_daily_log(record: dict[str, Any]) -> IngestHealthReport:
    return evaluate(
        source="scheduler:daily_log",
        records=[record],
        expectations=DAILY_LOG_EXPECTATIONS,
    )
