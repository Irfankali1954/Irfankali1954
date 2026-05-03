"""SAP S/4HANA connector stub.

Implements :class:`app.services.erp_bridge.ERPConnector` against the SAP
OData v2 services for PS (Project System), CO (Cost), and MM (Materials).
Auth via OAuth 2.0 with the BTP-issued client credentials.
"""

from __future__ import annotations

import httpx

from app.core.config import get_settings


class SAPERP:
    name = "sap"

    def __init__(self) -> None:
        s = get_settings()
        self._base_url = s.sap_s4_base_url
        self._client_id = s.sap_s4_client_id
        self._client_secret = s.sap_s4_client_secret

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self._base_url or "http://localhost", timeout=30)

    def pull_commitments(self, project_code: str) -> list[dict]:
        # TODO: GET /sap/opu/odata/sap/PROJECT_SRV/Projects('code')/Commitments
        return []

    def push_accruals(self, project_code: str, accruals: list[dict]) -> dict:
        # TODO: POST /sap/opu/odata/sap/CO_ACCRUAL_SRV/Accruals
        return {"status": "stub", "pushed": len(accruals)}
