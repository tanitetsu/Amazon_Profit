"""Unit tests for deploy secret source. Never uses real secret bytes."""

from __future__ import annotations

from app.deploy_secrets import pick_secret_source


def test_gcs_wins_over_local_and_generate() -> None:
    assert pick_secret_source(gcs_exists=True, local_exists=True) == "gcs"
    assert pick_secret_source(gcs_exists=True, local_exists=False) == "gcs"


def test_local_when_gcs_missing() -> None:
    assert pick_secret_source(gcs_exists=False, local_exists=True) == "local"


def test_generate_only_when_both_missing() -> None:
    assert pick_secret_source(gcs_exists=False, local_exists=False) == "generate"
