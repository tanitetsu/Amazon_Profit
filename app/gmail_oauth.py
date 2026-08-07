"""Per-user Gmail OAuth (gmail.readonly). Plan B: user opens invite link from email."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from app.google_clients import CLIENT_SECRETS, SECRETS
from app.schema import user_id_from_gmail
from app.sheets_retry import call_with_retry, execute_with_retry, is_auth_fatal, is_transient

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
]

TOKEN_DIR = SECRETS / "gmail_tokens"
STATE_DIR = SECRETS / "gmail_oauth_state"
INVITE_TTL_SEC = 7 * 24 * 3600  # 7 days


def public_base_url(request_url_root: str | None = None) -> str:
    """
    Base URL embedded in consent emails. Must be reachable by the end user.
    Set PUBLIC_BASE_URL (Cloud Run oauth service or tunnel). Local 127.0.0.1
    only works if the user opens the link on the same machine.
    """
    env = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if env:
        return env
    if request_url_root:
        return request_url_root.rstrip("/")
    port = (os.environ.get("PORT") or "5055").strip()
    return f"http://127.0.0.1:{port}"


def gmail_redirect_uri(base: str | None = None) -> str:
    return f"{(base or public_base_url()).rstrip('/')}/oauth/gmail/callback"


def _invite_secret() -> bytes:
    raw = (os.environ.get("GMAIL_INVITE_SECRET") or os.environ.get("FLASK_SECRET_KEY") or "").strip()
    if not raw:
        key_path = SECRETS / "gmail_invite_secret.txt"
        if key_path.is_file():
            raw = key_path.read_text(encoding="utf-8").strip()
        else:
            SECRETS.mkdir(parents=True, exist_ok=True)
            raw = secrets.token_urlsafe(32)
            key_path.write_text(raw + "\n", encoding="utf-8")
    return raw.encode("utf-8")


def make_invite_token(gmail: str, *, ttl_sec: int = INVITE_TTL_SEC) -> str:
    """Signed invite for email links (no admin session required)."""
    exp = int(time.time()) + ttl_sec
    payload = f"{gmail.strip().lower()}:{exp}"
    sig = hmac.new(_invite_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    raw = f"{payload}:{sig}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def parse_invite_token(token: str) -> str:
    """Returns gmail or raises ValueError."""
    pad = "=" * (-len(token) % 4)
    try:
        decoded = base64.urlsafe_b64decode(token + pad).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        raise ValueError("invalid invite token") from exc
    parts = decoded.rsplit(":", 2)
    if len(parts) != 3:
        raise ValueError("invalid invite token")
    gmail, exp_s, sig = parts
    payload = f"{gmail}:{exp_s}"
    expect = hmac.new(_invite_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, sig):
        raise ValueError("invalid invite signature")
    try:
        exp = int(exp_s)
    except ValueError as exc:
        raise ValueError("invalid invite expiry") from exc
    if time.time() > exp:
        raise ValueError("invite link expired")
    if "@" not in gmail:
        raise ValueError("invalid invite gmail")
    return gmail


def consent_start_url(gmail: str, *, base_url: str | None = None) -> str:
    base = (base_url or public_base_url()).rstrip("/")
    q = urlencode({"invite": make_invite_token(gmail)})
    return f"{base}/oauth/gmail/start?{q}"


def _app_config_gcs_uri() -> str:
    return (
        (os.environ.get("APP_CONFIG_GCS_URI") or "").strip()
        or (os.environ.get("USERS_CONFIG_GCS_URI") or "").strip()
    )


def _tokens_gcs_prefix() -> str | None:
    explicit = (os.environ.get("GMAIL_TOKENS_GCS_PREFIX") or "").strip().rstrip("/")
    if explicit:
        return explicit
    users_uri = _app_config_gcs_uri()
    if users_uri.startswith("gs://"):
        # gs://bucket/config/app_config.json → gs://bucket/gmail_tokens
        parts = users_uri[5:].split("/", 1)
        bucket = parts[0]
        return f"gs://{bucket}/gmail_tokens"
    return None


def _gcs_blob(uri: str):
    from google.cloud import storage

    assert uri.startswith("gs://")
    rest = uri[5:]
    bucket_name, _, blob_name = rest.partition("/")
    client = storage.Client()
    return client.bucket(bucket_name).blob(blob_name)


def token_path(gmail: str) -> Path:
    return TOKEN_DIR / f"{user_id_from_gmail(gmail)}.json"


def _token_gcs_uri(gmail: str) -> str | None:
    prefix = _tokens_gcs_prefix()
    if not prefix:
        return None
    return f"{prefix}/{user_id_from_gmail(gmail)}.json"


def has_gmail_token(gmail: str) -> bool:
    uri = _token_gcs_uri(gmail)
    if uri:
        return bool(
            call_with_retry(
                lambda: _gcs_blob(uri).exists(),
                label="gmail_token.exists",
            )
        )
    return token_path(gmail).is_file()


def load_gmail_credentials(gmail: str, *, force_refresh: bool = False) -> Credentials | None:
    """
    Load user Gmail OAuth. Access tokens (~1h) are refreshed via refresh_token.
    Google refresh tokens have no short TTL; they can be revoked or go stale after
    long unused periods — keep them warm by refreshing on each poll.
    """
    from datetime import datetime, timedelta, timezone

    text: str | None = None
    uri = _token_gcs_uri(gmail)
    if uri:
        blob = _gcs_blob(uri)
        if call_with_retry(blob.exists, label="gmail_token.exists"):
            text = call_with_retry(
                lambda: blob.download_as_text(encoding="utf-8"),
                label="gmail_token.download",
            )
    else:
        path = token_path(gmail)
        if path.is_file():
            text = path.read_text(encoding="utf-8")
    if not text:
        return None
    creds = Credentials.from_authorized_user_info(json.loads(text), GMAIL_SCOPES)
    if not creds or not creds.refresh_token:
        return None
    if not creds.has_scopes(GMAIL_SCOPES):
        return None

    need_refresh = force_refresh or (not creds.valid)
    if not need_refresh and creds.expiry:
        exp = creds.expiry
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        # Refresh early so poll never hits an expired access token mid-flight.
        if exp <= datetime.now(timezone.utc) + timedelta(minutes=10):
            need_refresh = True

    if need_refresh:
        try:
            def _refresh() -> None:
                creds.refresh(Request())
                save_gmail_credentials(gmail, creds)

            call_with_retry(_refresh, label="gmail.oauth.refresh")
        except Exception as exc:
            # Permanent revoke / invalid_grant → treat as unlinked.
            # Exhausted transient retries also return None so poll can continue
            # other users; maintain_gmail_token will surface the failure.
            if is_auth_fatal(exc) or not is_transient(exc):
                return None
            return None
    return creds


def maintain_gmail_token(gmail: str) -> dict[str, Any]:
    """
    Explicit keep-alive: refresh access token and touch Gmail API.
    Call from the 5-minute poll so refresh_token stays in active use.
    """
    creds = load_gmail_credentials(gmail, force_refresh=True)
    if not creds:
        raise RuntimeError(f"Gmail token missing or refresh failed: {gmail}")
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    profile = execute_with_retry(
        service.users().getProfile(userId="me"),
        label="gmail.users.getProfile",
    )
    return {
        "gmail": gmail,
        "emailAddress": (profile.get("emailAddress") or "").strip().lower(),
        "refreshed": True,
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
    }


def save_gmail_credentials(gmail: str, creds: Credentials) -> None:
    payload = creds.to_json()
    uri = _token_gcs_uri(gmail)
    if uri:
        call_with_retry(
            lambda: _gcs_blob(uri).upload_from_string(
                payload, content_type="application/json; charset=utf-8"
            ),
            label="gmail_token.upload",
        )
        return
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    token_path(gmail).write_text(payload, encoding="utf-8")


def delete_gmail_credentials(gmail: str) -> bool:
    uri = _token_gcs_uri(gmail)
    if uri:
        blob = _gcs_blob(uri)

        def _delete() -> bool:
            if not blob.exists():
                return False
            blob.delete()
            return True

        return bool(call_with_retry(_delete, label="gmail_token.delete"))
    path = token_path(gmail)
    if path.is_file():
        path.unlink()
        return True
    return False


def list_linked_gmails(candidate_gmails: list[str]) -> list[str]:
    return [g for g in candidate_gmails if g and has_gmail_token(g)]


def _allow_http_localhost(base: str | None = None) -> None:
    if (os.environ.get("OAUTHLIB_INSECURE_TRANSPORT") or "").strip():
        return
    b = (base or public_base_url()).rstrip("/")
    if b.startswith("http://127.0.0.1") or b.startswith("http://localhost"):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


def _oauth_client_gcs_uri() -> str | None:
    explicit = (os.environ.get("OAUTH_CLIENT_GCS_URI") or "").strip()
    if explicit.startswith("gs://"):
        return explicit
    users_uri = _app_config_gcs_uri()
    if users_uri.startswith("gs://"):
        bucket = users_uri[5:].split("/", 1)[0]
        return f"gs://{bucket}/secrets/oauth_client.json"
    return None


def load_oauth_client_config() -> dict[str, Any]:
    """OAuth client JSON (web preferred for Cloud Run callback; installed for local)."""
    uri = _oauth_client_gcs_uri()
    if uri:
        blob = _gcs_blob(uri)
        if call_with_retry(blob.exists, label="oauth_client.gcs.exists"):
            text = call_with_retry(
                lambda: blob.download_as_text(encoding="utf-8"),
                label="oauth_client.gcs.download",
            )
            return json.loads(text)
    if CLIENT_SECRETS.exists():
        return json.loads(CLIENT_SECRETS.read_text(encoding="utf-8"))
    raise FileNotFoundError(
        f"Missing OAuth client. Set OAUTH_CLIENT_GCS_URI or place {CLIENT_SECRETS}. "
        "Cloud Run consent needs a Web client with redirect "
        f"{gmail_redirect_uri()}."
    )


def _new_flow(
    *,
    redirect_uri: str,
    state: str | None = None,
    code_verifier: str | None = None,
) -> Flow:
    config = load_oauth_client_config()
    # PKCE: same code_verifier must be used at authorize + token exchange.
    kwargs: dict[str, Any] = {
        "scopes": GMAIL_SCOPES,
        "state": state,
        "redirect_uri": redirect_uri,
    }
    if code_verifier:
        kwargs["code_verifier"] = code_verifier
        kwargs["autogenerate_code_verifier"] = False
    else:
        kwargs["autogenerate_code_verifier"] = True
    return Flow.from_client_config(config, **kwargs)


def _state_gcs_prefix() -> str | None:
    explicit = (os.environ.get("GMAIL_OAUTH_STATE_GCS_PREFIX") or "").strip().rstrip("/")
    if explicit:
        return explicit
    users_uri = _app_config_gcs_uri()
    if users_uri.startswith("gs://"):
        bucket = users_uri[5:].split("/", 1)[0]
        return f"gs://{bucket}/gmail_oauth_state"
    return None


def _save_pending_state(
    state: str, gmail: str, *, code_verifier: str | None = None
) -> None:
    payload: dict[str, Any] = {"gmail": gmail.strip().lower()}
    if code_verifier:
        payload["code_verifier"] = code_verifier
    body = json.dumps(payload, ensure_ascii=False)
    prefix = _state_gcs_prefix()
    if prefix:
        call_with_retry(
            lambda: _gcs_blob(f"{prefix}/{state}.json").upload_from_string(
                body, content_type="application/json; charset=utf-8"
            ),
            label="gmail_oauth_state.upload",
        )
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"{state}.json").write_text(body, encoding="utf-8")


def _pop_pending_state(state: str) -> dict[str, Any] | None:
    prefix = _state_gcs_prefix()
    if prefix:
        blob = _gcs_blob(f"{prefix}/{state}.json")
        if not call_with_retry(blob.exists, label="gmail_oauth_state.exists"):
            return None
        try:
            data = json.loads(
                call_with_retry(
                    lambda: blob.download_as_text(encoding="utf-8"),
                    label="gmail_oauth_state.download",
                )
            )
        finally:
            try:
                call_with_retry(blob.delete, label="gmail_oauth_state.delete")
            except Exception:  # noqa: BLE001
                pass
        gmail = (data.get("gmail") or "").strip().lower()
        if not gmail:
            return None
        return {
            "gmail": gmail,
            "code_verifier": (data.get("code_verifier") or None),
        }

    path = STATE_DIR / f"{state}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        gmail = (data.get("gmail") or "").strip().lower()
        if not gmail:
            return None
        return {
            "gmail": gmail,
            "code_verifier": (data.get("code_verifier") or None),
        }
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def build_gmail_auth_url(
    gmail: str,
    *,
    base_url: str | None = None,
) -> tuple[str, str]:
    """Returns (authorization_url, state) for the end-user consent screen."""
    _allow_http_localhost(base_url)
    gmail = gmail.strip()
    redirect_uri = gmail_redirect_uri(base_url)
    state = secrets.token_urlsafe(24)
    # 43–128 chars; urlsafe token is fine for PKCE
    code_verifier = secrets.token_urlsafe(64)
    flow = _new_flow(
        redirect_uri=redirect_uri, state=state, code_verifier=code_verifier
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        login_hint=gmail,
    )
    # Prefer verifier actually attached to the flow after authorization_url
    verifier = getattr(flow, "code_verifier", None) or code_verifier
    _save_pending_state(state, gmail, code_verifier=verifier)
    return auth_url, state


def finish_gmail_oauth(
    *,
    state: str,
    authorization_response: str,
    base_url: str | None = None,
) -> dict[str, Any]:
    _allow_http_localhost(base_url)
    pending = _pop_pending_state(state)
    if not pending:
        raise ValueError("invalid or expired OAuth state")
    expected = pending["gmail"]
    code_verifier = pending.get("code_verifier")

    redirect_uri = gmail_redirect_uri(base_url)
    flow = _new_flow(
        redirect_uri=redirect_uri,
        state=state,
        code_verifier=code_verifier,
    )
    if code_verifier:
        flow.code_verifier = code_verifier
    flow.fetch_token(authorization_response=authorization_response)
    creds = flow.credentials

    gmail_api = build("gmail", "v1", credentials=creds, cache_discovery=False)
    profile = execute_with_retry(
        gmail_api.users().getProfile(userId="me"),
        label="gmail.users.getProfile.oauth",
    )
    actual = (profile.get("emailAddress") or "").strip().lower()
    if actual != expected:
        raise ValueError(
            f"consented account is {actual or '(unknown)'}, expected {expected}"
        )

    save_gmail_credentials(expected, creds)
    return {
        "gmail": expected,
        "emailAddress": actual,
        "messagesTotal": profile.get("messagesTotal"),
        "historyId": profile.get("historyId"),
    }
