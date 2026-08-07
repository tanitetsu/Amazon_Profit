"""Helpers shared by mail parse + legacy Excel for order line cleanup."""

from __future__ import annotations

import re

# Amazon multi-item order mails / legacy sheets put this headline into SKU.
_MULTI_SOLD_SKU_RE = re.compile(
    r"^\d+\s*点の商品が販売されました\s*$"
)


def is_placeholder_sku(sku: str | None) -> bool:
    s = (sku or "").strip()
    if not s:
        return False
    if _MULTI_SOLD_SKU_RE.match(s):
        return True
    if "点の商品が販売" in s:
        return True
    return False


def normalize_sku(sku: str | None) -> str:
    s = (sku or "").strip()
    if not s or is_placeholder_sku(s):
        return ""
    return s
