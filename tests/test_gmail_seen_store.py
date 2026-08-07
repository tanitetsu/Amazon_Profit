"""Unit tests for gmail_seen local + GCS persistence helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import mail_ingest as mi


def test_seen_local_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mi, "SEEN_DIR", tmp_path)
    monkeypatch.delenv("APP_CONFIG_GCS_URI", raising=False)
    monkeypatch.delenv("USERS_CONFIG_GCS_URI", raising=False)
    monkeypatch.delenv("GMAIL_SEEN_GCS_PREFIX", raising=False)

    gmail = "asamiodaka.b@gmail.com"
    assert mi.load_seen_ids(gmail) == set()
    mi.save_seen_ids(gmail, {"a", "b", "c"})
    assert mi.load_seen_ids(gmail) == {"a", "b", "c"}
    assert mi.clear_seen_ids(gmail) is True
    assert mi.load_seen_ids(gmail) == set()
    assert mi.clear_seen_ids(gmail) is False


def test_seen_gcs_roundtrip(monkeypatch) -> None:
    monkeypatch.setenv(
        "APP_CONFIG_GCS_URI",
        "gs://positive-design-480606-c7-amazon-profit-admin/config/app_config.json",
    )
    monkeypatch.delenv("GMAIL_SEEN_GCS_PREFIX", raising=False)

    store: dict[str, str] = {}
    blob = MagicMock()

    def exists() -> bool:
        return "data" in store

    def download_as_text(*, encoding: str = "utf-8") -> str:
        return store["data"]

    def upload_from_string(payload: str, content_type: str = "") -> None:
        store["data"] = payload

    def delete() -> None:
        store.pop("data", None)

    blob.exists.side_effect = exists
    blob.download_as_text.side_effect = download_as_text
    blob.upload_from_string.side_effect = upload_from_string
    blob.delete.side_effect = delete

    with patch.object(mi, "_gcs_blob", return_value=blob):
        gmail = "tracaude@gmail.com"
        assert mi._seen_gcs_uri(gmail).endswith("/gmail_seen/tracaude.json")
        assert mi.load_seen_ids(gmail) == set()
        mi.save_seen_ids(gmail, {"m1", "m2"})
        assert json.loads(store["data"])["ids"] == ["m1", "m2"] or set(
            json.loads(store["data"])["ids"]
        ) == {"m1", "m2"}
        assert mi.load_seen_ids(gmail) == {"m1", "m2"}
        assert mi.clear_seen_ids(gmail) is True
        assert mi.load_seen_ids(gmail) == set()
