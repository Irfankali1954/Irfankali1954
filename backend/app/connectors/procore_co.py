"""Procore Change Events connector stub.

Internal EPC dev teams use this path to push change events from Procore
directly into the Sentinel. Production wires to the Procore REST API at
``GET /rest/v1.0/projects/{id}/change_events``; the stub returns whatever
records were handed in so tests + dev runs work offline.
"""

from __future__ import annotations


class ProcoreChangeEvents:
    name = "procore"

    def list_change_events(self, project_code: str) -> list[dict]:
        # TODO: hit Procore /rest/v1.0/projects/{project_code}/change_events
        return []
