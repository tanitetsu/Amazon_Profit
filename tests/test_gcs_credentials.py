"""Unit tests for path-or-JSON GCS credentials. Never uses real SA keys."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from app import gcs_credentials as gc

_FAKE_SA = {
    "type": "service_account",
    "project_id": "unit-test",
    "client_email": "sa@unit-test.iam.gserviceaccount.com",
}


def _clear_cred_env(monkeypatch) -> None:
    monkeypatch.delenv("AIC_GCS_CREDENTIALS", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)


def test_materialize_existing_file_path(tmp_path: Path) -> None:
    dest = tmp_path / "sa.json"
    dest.write_text(json.dumps(_FAKE_SA), encoding="utf-8")
    assert gc.materialize_credentials_value(str(dest)) == str(dest)


def test_materialize_json_body_writes_temp_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(gc.tempfile, "gettempdir", lambda: str(tmp_path))
    raw = json.dumps(_FAKE_SA)
    path = gc.materialize_credentials_value(raw)
    assert path is not None
    written = Path(path)
    assert written.is_file()
    assert written.name == gc._MATERIALIZED_NAME
    loaded = json.loads(written.read_text(encoding="utf-8"))
    assert loaded["type"] == "service_account"
    assert loaded["project_id"] == "unit-test"
    assert "private_key" not in loaded


def test_materialize_invalid_json_returns_none(caplog) -> None:
    caplog.set_level(logging.WARNING)
    assert gc.materialize_credentials_value("{not-json") is None
    assert "not valid JSON" in caplog.text
    assert "private_key" not in caplog.text
    assert "{not-json" not in caplog.text


def test_materialize_plain_string_returns_none() -> None:
    assert gc.materialize_credentials_value("not-a-path-or-json") is None


def test_resolve_json_in_aic_sets_application_credentials(
    tmp_path: Path, monkeypatch
) -> None:
    _clear_cred_env(monkeypatch)
    monkeypatch.setattr(gc.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setenv("AIC_GCS_CREDENTIALS", json.dumps(_FAKE_SA))
    path = gc.resolve_gcs_credentials_path(root=tmp_path)
    assert path is not None
    assert Path(path).is_file()
    assert Path(path).read_text(encoding="utf-8").startswith("{")
    assert os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") == path


def test_resolve_prefers_aic_file_over_gac(tmp_path: Path, monkeypatch) -> None:
    _clear_cred_env(monkeypatch)
    aic = tmp_path / "aic.json"
    gac = tmp_path / "gac.json"
    aic.write_text(json.dumps({**_FAKE_SA, "project_id": "aic"}), encoding="utf-8")
    gac.write_text(json.dumps({**_FAKE_SA, "project_id": "gac"}), encoding="utf-8")
    monkeypatch.setenv("AIC_GCS_CREDENTIALS", str(aic))
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(gac))
    assert gc.resolve_gcs_credentials_path(root=tmp_path) == str(aic)


def test_resolve_falls_back_to_local_secrets(tmp_path: Path, monkeypatch) -> None:
    _clear_cred_env(monkeypatch)
    local = tmp_path / "secrets" / "aic_gcs_service_account.json"
    local.parent.mkdir(parents=True)
    local.write_text(json.dumps(_FAKE_SA), encoding="utf-8")
    assert gc.resolve_gcs_credentials_path(root=tmp_path) == str(local)


def test_clipping_roster_accepts_json_env(tmp_path: Path, monkeypatch) -> None:
    from app import clipping_roster as cr

    _clear_cred_env(monkeypatch)
    monkeypatch.setattr(gc.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setenv("AIC_GCS_CREDENTIALS", json.dumps(_FAKE_SA))
    path = cr._resolve_credentials_path()
    assert path is not None
    assert Path(path).is_file()
