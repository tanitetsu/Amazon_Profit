"""Decide where Cloud Run runtime secrets come from. Never log secret bytes."""

from __future__ import annotations

from typing import Literal

SecretSource = Literal["gcs", "local", "generate"]


def pick_secret_source(*, gcs_exists: bool, local_exists: bool) -> SecretSource:
    """GCS wins so a Cloud Agent without local files cannot rotate production secrets."""
    if gcs_exists:
        return "gcs"
    if local_exists:
        return "local"
    return "generate"
