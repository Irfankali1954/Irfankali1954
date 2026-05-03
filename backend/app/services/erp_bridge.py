"""Bi-directional ERP bridge.

Sits between the project domain model and Oracle Fusion / SAP S/4HANA.
Inbound (pull): commitments, POs, vendor invoices, paid-to-date.
Outbound (push): accruals, change orders, claim values, COD updates.

Each connector implements :class:`ERPConnector`. The bridge fans out without
caring which ERP is on the other side.
"""

from __future__ import annotations

from typing import Protocol

from app.connectors.oracle_erp import OracleERP
from app.connectors.sap_erp import SAPERP


class ERPConnector(Protocol):
    name: str
    def pull_commitments(self, project_code: str) -> list[dict]: ...
    def push_accruals(self, project_code: str, accruals: list[dict]) -> dict: ...


def get_connector(vendor: str) -> ERPConnector:
    vendor = vendor.lower()
    if vendor == "oracle":
        return OracleERP()
    if vendor == "sap":
        return SAPERP()
    raise ValueError(f"unknown ERP vendor: {vendor}")


def sync_project(vendor: str, project_code: str) -> dict:
    """One-shot bi-directional sync. Returns a summary dict."""
    conn = get_connector(vendor)
    commitments = conn.pull_commitments(project_code)
    push_result = conn.push_accruals(project_code, accruals=[])
    return {
        "vendor": conn.name,
        "project_code": project_code,
        "commitments_pulled": len(commitments),
        "push_status": push_result.get("status", "unknown"),
    }
