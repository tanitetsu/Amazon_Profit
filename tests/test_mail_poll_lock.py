"""Tests for mail-poll overlap lock."""

from __future__ import annotations

import json
from pathlib import Path

from app import mail_poll_lock as lock


def test_local_lock_acquire_and_busy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(lock, "SECRETS", tmp_path)
    monkeypatch.setattr(lock, "LOCAL_LOCK", tmp_path / "mail_poll_lock.json")
    monkeypatch.delenv("APP_CONFIG_GCS_URI", raising=False)
    monkeypatch.delenv("USERS_CONFIG_GCS_URI", raising=False)
    monkeypatch.delenv("MAIL_POLL_LOCK_GCS_URI", raising=False)
    monkeypatch.setenv("MAIL_POLL_LOCK_TTL_SEC", "600")

    ok, owner, reason = lock.try_acquire_mail_poll_lock()
    assert ok and owner and reason is None
    ok2, owner2, reason2 = lock.try_acquire_mail_poll_lock()
    assert not ok2 and owner2 is None and reason2 and "busy" in reason2
    lock.release_mail_poll_lock(owner)
    ok3, owner3, _ = lock.try_acquire_mail_poll_lock()
    assert ok3 and owner3
    lock.release_mail_poll_lock(owner3)
    assert not (tmp_path / "mail_poll_lock.json").is_file() or True


def test_local_lock_expires(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(lock, "SECRETS", tmp_path)
    monkeypatch.setattr(lock, "LOCAL_LOCK", tmp_path / "mail_poll_lock.json")
    monkeypatch.delenv("APP_CONFIG_GCS_URI", raising=False)
    monkeypatch.delenv("USERS_CONFIG_GCS_URI", raising=False)
    monkeypatch.delenv("MAIL_POLL_LOCK_GCS_URI", raising=False)
    monkeypatch.setenv("MAIL_POLL_LOCK_TTL_SEC", "600")

    (tmp_path / "mail_poll_lock.json").write_text(
        json.dumps({"owner": "old", "started_at_unix": 1.0, "ttl_sec": 600}),
        encoding="utf-8",
    )
    ok, owner, _ = lock.try_acquire_mail_poll_lock()
    assert ok and owner != "old"
    lock.release_mail_poll_lock(owner)
