"""MS Project connector stub.

For .MPP file ingestion see :mod:`app.services.mpp_parser`. This module is
for Project Online / Project for the Web via Microsoft Graph.
"""

from __future__ import annotations


class MSProject:
    name = "msp"

    def list_activities(self, project_code: str) -> list[dict]:
        # TODO: GET https://graph.microsoft.com/v1.0/planner/...
        return []
