# EPC Master-Wrap Agent — Project Overview

Cross-organizational intelligence layer for major EPC firms (Kiewit, Bechtel-class)
to eliminate **Execution Drift** across the Lead Contractor, Subcontractors, and
External Engineering firms.

## Repository layout

```
backend/    FastAPI service — domain logic, RBAC, ERP/EDC connectors, risk engine
frontend/   Next.js (App Router) — CFO Command Center, Gantt, Risk dashboards
docs/       Architecture notes
```

## Priority modules (scaffolded)

| # | Module                                                | Backend entry                  | Frontend entry              |
|---|-------------------------------------------------------|--------------------------------|-----------------------------|
| 1 | CFO Command Center / Financial Gatekeeper             | `backend/app/api/v1/cfo.py`        | `frontend/app/cfo/page.tsx`     |
| 2 | ERP / Gantt Bridge (P6 .XER, MSP .MPP, Oracle, SAP)   | `backend/app/api/v1/erp.py`        | `frontend/app/erp/page.tsx`     |
| 3 | Autonomous Scheduler (Daily Log → Gantt → CPM)        | `backend/app/api/v1/scheduler.py`  | `frontend/app/schedule/page.tsx`|
| 4 | Master-Wrap Risk Engine (Field Idle Cost, Wrap Score) | `backend/app/api/v1/risk.py`       | `frontend/app/risk/page.tsx`    |
| 5 | Federated RBAC (Admin owns tech, CFO owns visibility) | `backend/app/core/rbac.py`         | `frontend/app/admin/page.tsx`   |

## Quick start

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload

# Frontend
cd frontend
npm install
npm run dev
```

API docs: http://localhost:8000/docs · UI: http://localhost:3000

## Design pillars

- **Margin-masking by default** — financial fields are stripped at the schema
  boundary based on a CFO-controlled `VisibilityPolicy`, not at the UI.
- **Two-axis RBAC** — *technical* permissions (managed by Admin) are decoupled
  from *visibility* permissions (managed by CFO). See `backend/app/core/rbac.py`.
- **Bi-directional ERP** — connectors implement a common `ERPConnector`
  protocol so Oracle/SAP swaps are localized.
- **Idempotent daily-log ingestion** — voice-to-text payloads update activities
  and trigger CPM recompute via the autonomous scheduler.
