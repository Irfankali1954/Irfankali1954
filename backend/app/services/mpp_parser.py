"""MS Project .MPP ingestion stub.

.MPP is a proprietary OLE compound document. Production options:

* MPXJ via Jython / Java sidecar (recommended)
* MS Project Online's OData API (preferred when available)

Stub keeps the contract identical to :mod:`app.services.xer_parser`.
"""

from __future__ import annotations

from app.services.xer_parser import ParsedActivity


def parse_mpp(content: bytes) -> list[ParsedActivity]:
    """TODO: shell out to MPXJ sidecar; for now returns []."""
    return []
