"""Margin-masking serializer.

Any schema with a ``MASK_FIELDS`` class attribute (mapping API field name →
:class:`FinancialField`) is run through :func:`apply_visibility` before it
leaves the API. Fields the caller's role is not allowed to see are nulled.

This is enforced server-side so the data never reaches the client. The CFO
controls the policy; even the Admin role gets masked unless explicitly
granted.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from app.core.rbac import (
    FinancialField,
    TechnicalRole,
    VisibilityPolicy,
    default_visibility_policy,
)

T = TypeVar("T", bound=BaseModel)

# Module-level singleton; replaced at runtime by the CFO via the policy API.
_active_policy: VisibilityPolicy = default_visibility_policy()


def get_policy() -> VisibilityPolicy:
    return _active_policy


def set_policy(policy: VisibilityPolicy) -> None:
    global _active_policy
    _active_policy = policy


def apply_visibility(model: T, role: TechnicalRole) -> T:
    """Return a copy of *model* with disallowed financial fields nulled."""
    mask_map: dict[str, FinancialField] = getattr(type(model), "MASK_FIELDS", {})
    if not mask_map:
        return model
    allowed = _active_policy.fields_for(role)
    updates: dict[str, Any] = {}
    for field_name, fin_field in mask_map.items():
        if fin_field not in allowed:
            updates[field_name] = None
    return model.model_copy(update=updates) if updates else model


def apply_many(models: list[T], role: TechnicalRole) -> list[T]:
    return [apply_visibility(m, role) for m in models]
