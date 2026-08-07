"""AI_Cripping-compatible app roles (not Drive share permissions)."""

from __future__ import annotations

ROLE_ADMIN = "Admin"
ROLE_EXCLUSIVE = "Exclusive"
ROLE_NORMAL = "Normal"
VALID_ROLES = frozenset({ROLE_ADMIN, ROLE_EXCLUSIVE, ROLE_NORMAL})
DEFAULT_ROLE = ROLE_NORMAL


def normalize_app_role(role: str | None) -> str:
    text = str(role or "").strip()
    if not text:
        return DEFAULT_ROLE
    for known in VALID_ROLES:
        if text.lower() == known.lower():
            return known
    raise ValueError(
        f"invalid role: {role!r} (want one of {sorted(VALID_ROLES)})"
    )
