"""Primavera P6 connector stub.

For .XER file ingestion see :mod:`app.services.xer_parser`. This module is
for the live P6 EPPM REST API (POST /authenticate, GET /activities, etc.).
"""

from __future__ import annotations


class PrimaveraP6:
    name = "p6"

    def list_activities(self, project_code: str) -> list[dict]:
        # TODO: hit /api/restapi/activity?projectId=...
        return []
