"""Mercari helpers from Amazon SKU (item URL + live price via items/get)."""
from __future__ import annotations
import logging
import random
import re
import threading
import time
import uuid
from typing import Any
from urllib.parse import urlencode
import httpx
from ecdsa import NIST256p, SigningKey
from jose import jws
from jose.backends.ecdsa_backend import ECDSAECKey
from jose.constants import ALGORITHMS
from app.sheets_retry import is_transient
_SKU_RE = re.compile(r"^m_m(\d+)$")
_ITEMS_GET = "https://api.mercari.jp/items/get"
_TIMEOUT_SEC = 8.0
_MAX_ATTEMPTS = 5
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
_log = logging.getLogger(__name__)
_lock = threading.Lock()
_signing_key: SigningKey | None = None
_client_uuid: str | None = None
def mercari_item_id_from_sku(sku: str | None) -> str | None:
    """m_m42688576128 → m42688576128. Other formats → None."""
    if not sku:
        return None
    m = _SKU_RE.match(str(sku).strip())
    if not m:
        return None
    return f"m{m.group(1)}"
def mercari_item_url(sku: str | None) -> str | None:
    item_id = mercari_item_id_from_sku(sku)
    if not item_id:
        return None
    return f"https://jp.mercari.com/item/{item_id}"
def _ensure_signing_state() -> tuple[SigningKey, str]:
    global _signing_key, _client_uuid
    with _lock:
        if _signing_key is None:
            _signing_key = SigningKey.generate(NIST256p)
        if _client_uuid is None:
            _client_uuid = str(uuid.UUID(int=random.getrandbits(128)))
        return _signing_key, _client_uuid
def _generate_dpop(url: str, method: str, key: SigningKey, client_uuid: str) -> str:
    payload: dict[str, Any] = {
        "iat": int(time.time()),
        "jti": str(uuid.UUID(int=random.getrandbits(128))),
        "htu": url,
        "htm": method,
        "uuid": client_uuid,
    }
    ec_key = ECDSAECKey(key, ALGORITHMS.ES256)
    headers = {
        "typ": "dpop+jwt",
        "alg": "ES256",
        "jwk": {k: ec_key.to_dict()[k] for k in ["crv", "kty", "x", "y"]},
    }
    return jws.sign(payload, key, headers, ALGORITHMS.ES256)
def _http_transient_status(status: int) -> bool:
    return status in (408, 429, 500, 502, 503, 504)
def _fetch_item_price_by_id(item_id: str) -> int | None:
    """GET api.mercari.jp/items/get with DPoP. Returns yen int or None."""
    query = urlencode({"id": item_id})
    url = f"{_ITEMS_GET}?{query}"
    last_err: str | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        key, client_uuid = _ensure_signing_state()
        headers = {
            "User-Agent": _USER_AGENT,
            "X-Platform": "web",
            "Accept": "application/json",
            "DPoP": _generate_dpop(url, "GET", key, client_uuid),
        }
        try:
            with httpx.Client(timeout=_TIMEOUT_SEC) as client:
                res = client.get(url, headers=headers)
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
            if is_transient(exc) and attempt < _MAX_ATTEMPTS:
                delay = min(30.0, 0.8 * (2 ** (attempt - 1)))
                _log.warning(
                    "mercari items/get transient for %s "
                    "(attempt %s/%s), sleep %.1fs - %s",
                    item_id,
                    attempt,
                    _MAX_ATTEMPTS,
                    delay,
                    last_err,
                )
                time.sleep(delay)
                continue
            _log.warning("mercari items/get failed for %s: %s", item_id, exc)
            return None
        if res.status_code == 404:
            _log.info("mercari item not found: %s", item_id)
            return None
        if _http_transient_status(res.status_code):
            last_err = f"HTTP {res.status_code}: {(res.text or '')[:200]}"
            if attempt < _MAX_ATTEMPTS:
                delay = min(30.0, 0.8 * (2 ** (attempt - 1)))
                _log.warning(
                    "mercari items/get %s for %s "
                    "(attempt %s/%s), sleep %.1fs",
                    res.status_code,
                    item_id,
                    attempt,
                    _MAX_ATTEMPTS,
                    delay,
                )
                time.sleep(delay)
                continue
            _log.warning("mercari items/get HTTP %s for %s: %s", res.status_code, item_id, last_err)
            return None
        if res.status_code != 200:
            _log.warning(
                "mercari items/get HTTP %s for %s: %s",
                res.status_code,
                item_id,
                (res.text or "")[:200],
            )
            return None
        try:
            body = res.json()
        except Exception as exc:  # noqa: BLE001
            _log.warning("mercari items/get bad JSON for %s: %s", item_id, exc)
            return None
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            _log.warning("mercari items/get missing data for %s", item_id)
            return None
        price = data.get("price")
        try:
            yen = int(price)
        except (TypeError, ValueError):
            _log.warning("mercari items/get bad price for %s: %r", item_id, price)
            return None
        if yen < 0:
            return None
        return yen
    if last_err:
        _log.warning("mercari items/get exhausted retries for %s: %s", item_id, last_err)
    return None
def fetch_mercari_price(sku: str | None) -> int | None:
    """
    Live listing price (JPY) for SKU m_m{digits}.
    Non-target SKU / API failure → None (caller leaves 仕入金 empty).
    """
    item_id = mercari_item_id_from_sku(sku)
    if item_id is None:
        return None
    return _fetch_item_price_by_id(item_id)
