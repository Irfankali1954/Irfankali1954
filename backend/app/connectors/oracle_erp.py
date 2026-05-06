"""Oracle Fusion ERP connector stub.

Implements :class:`app.services.erp_bridge.ERPConnector`. Wraps the Oracle
REST API for Project Financials and AP/AR. OAuth via Fusion Identity Cloud.
"""

from __future__ import annotations

import httpx

from app.core.config import get_settings


class OracleERP:
    name = "oracle"

    def __init__(self) -> None:
        s = get_settings()
        self._base_url = s.oracle_erp_base_url
        self._client_id = s.oracle_erp_client_id
        self._client_secret = s.oracle_erp_client_secret

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self._base_url or "http://localhost", timeout=30)

    def pull_commitments(self, project_code: str) -> list[dict]:
        # TODO: GET /projectFinancialsApi/.../commitments?project={code}
        return []

    def push_accruals(self, project_code: str, accruals: list[dict]) -> dict:
        # TODO: POST /projectFinancialsApi/.../accruals
        return {"status": "stub", "pushed": len(accruals)}
