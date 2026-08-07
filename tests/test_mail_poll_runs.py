"""Unit tests for mail_poll_runs local persistence and filters."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from app import mail_poll_runs as mpr


def _sample_summary(*, gmail: str = "alice@gmail.com", ok: bool = True) -> dict:
    return {
        "ok": ok,
        "started_at": "2026-08-04T12:00:00",
        "finished_at": "2026-08-04T12:00:05",
        "linked_users": 1,
        "errors": 0 if ok else 1,
        "max_workers": 2,
        "operator_token": {"ok": True, "expiry": "2026-08-04T13:00:00"},
        "results": [
            {
                "gmail": gmail,
                "ok": ok,
                "processed": 2 if ok else 0,
                "parse_miss": 0,
                "skipped_seen": 3,
                "token": {"gmail": gmail, "refreshed": True, "expiry": "2026-08-04T13:00:00"},
                **({"error": "boom"} if not ok else {}),
            }
        ],
    }


def test_local_save_list_filter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mpr, "RUNS_DIR", tmp_path)
    monkeypatch.delenv("APP_CONFIG_GCS_URI", raising=False)
    monkeypatch.delenv("USERS_CONFIG_GCS_URI", raising=False)
    monkeypatch.delenv("MAIL_POLL_RUNS_GCS_PREFIX", raising=False)
    monkeypatch.setattr(mpr, "_resolve_gcs_credentials_path", lambda: None)

    fixed = date(2026, 8, 4)
    with patch.object(
        mpr, "now_jst", return_value=datetime(2026, 8, 4, 12, 0, 5, tzinfo=mpr.JST)
    ):
        rec1 = mpr.save_poll_run(_sample_summary(gmail="alice@gmail.com"), retain_days=30)
        rec2 = mpr.save_poll_run(
            _sample_summary(gmail="bob@gmail.com", ok=False), retain_days=30
        )

    assert rec1 and rec1["run_id"]
    assert rec1["results"][0]["user_id"] == "alice"
    assert "token" not in rec1["results"][0]
    assert rec1["results"][0]["token_refreshed"] is True

    all_runs = mpr.list_runs_for_date(fixed)
    assert len(all_runs) == 2

    alice_only = mpr.list_runs_for_date(fixed, user_id="alice")
    assert len(alice_only) == 1
    assert alice_only[0]["results"][0]["user_id"] == "alice"

    bob_only = mpr.list_runs_for_date(fixed, user_id="bob")
    assert len(bob_only) == 1
    assert bob_only[0]["ok"] is False

    errors_only = mpr.list_runs_for_date(fixed, errors_only=True)
    assert len(errors_only) == 1
    assert errors_only[0]["run_id"] == rec2["run_id"]

    got = mpr.get_run(rec2["run_id"])
    assert got and got["run_id"] == rec2["run_id"]


def test_list_runs_without_date_scans_retention(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mpr, "RUNS_DIR", tmp_path)
    monkeypatch.delenv("APP_CONFIG_GCS_URI", raising=False)
    monkeypatch.delenv("USERS_CONFIG_GCS_URI", raising=False)
    monkeypatch.delenv("MAIL_POLL_RUNS_GCS_PREFIX", raising=False)
    monkeypatch.setattr(mpr, "_resolve_gcs_credentials_path", lambda: None)

    with patch.object(
        mpr, "now_jst", return_value=datetime(2026, 8, 4, 12, 0, 5, tzinfo=mpr.JST)
    ):
        mpr.save_poll_run(_sample_summary(gmail="alice@gmail.com"), retain_days=30)

    with patch.object(
        mpr, "now_jst", return_value=datetime(2026, 8, 3, 12, 0, 5, tzinfo=mpr.JST)
    ):
        mpr.save_poll_run(
            _sample_summary(gmail="bob@gmail.com", ok=False), retain_days=30
        )

    with patch.object(
        mpr, "now_jst", return_value=datetime(2026, 8, 4, 18, 0, 0, tzinfo=mpr.JST)
    ):
        all_days = mpr.list_runs(day=None, retain_days=30)
        err_days = mpr.list_runs(day=None, errors_only=True, retain_days=30)

    assert len(all_days) == 2
    assert len(err_days) == 1
    assert err_days[0]["ok"] is False
    assert err_days[0]["results"][0]["user_id"] == "bob"


def test_prune_drops_aged_day(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mpr, "RUNS_DIR", tmp_path)
    monkeypatch.delenv("APP_CONFIG_GCS_URI", raising=False)
    monkeypatch.delenv("USERS_CONFIG_GCS_URI", raising=False)
    monkeypatch.delenv("MAIL_POLL_RUNS_GCS_PREFIX", raising=False)
    monkeypatch.setattr(mpr, "_resolve_gcs_credentials_path", lambda: None)

    old = date(2026, 7, 1)
    old_dir = tmp_path / old.isoformat()
    old_dir.mkdir(parents=True)
    (old_dir / "20260701T120000_abcdef.json").write_text("{}", encoding="utf-8")

    # Sliding prune deletes exactly (today - retain_days).
    retain = (date(2026, 8, 4) - old).days
    with patch.object(
        mpr, "now_jst", return_value=datetime(2026, 8, 4, 12, 0, 0, tzinfo=mpr.JST)
    ):
        deleted = mpr.prune_old_runs(retain_days=retain)

    assert deleted == 1
    assert not old_dir.exists()


def test_parse_run_date_optional() -> None:
    assert mpr.parse_run_date(None) is None
    assert mpr.parse_run_date("") is None
    assert mpr.parse_run_date("2026-08-01") == date(2026, 8, 1)
    assert mpr.parse_errors_only("1") is True
    assert mpr.parse_errors_only("") is False
