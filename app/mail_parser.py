"""Parse Amazon seller notification emails (no Sheets write)."""

from __future__ import annotations

import base64
import email
import re
from dataclasses import dataclass, field
from email import policy
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.order_sku import is_placeholder_sku, normalize_sku
from app.schema import points_fallback

ORDER_ID_RE = re.compile(r"\b(\d{3}-\d{7}-\d{7})\b")
MONEY_RE = re.compile(r"[￥¥]\s*([0-9,]+)")
_SKU_LABEL_RE = re.compile(r"(?:出品者)?SKU\s*[:：]\s*(\S+)", re.I)
_PRODUCT_SPLIT_RE = re.compile(r"商品\s*[:：]\s*")
_MULTI_SOLD_HEADLINE_RE = re.compile(r"\d+\s*点の商品が販売されました")


@dataclass
class OrderLine:
    sku: str = ""
    title: str = ""
    qty: int = 1
    price: int | None = None
    tax: int | None = None
    fee: int | None = None
    points: int | None = None
    proceeds: int | None = None


@dataclass
class ParsedMail:
    kind: str  # order | cancel_request | return_approved | atoz
    subject: str
    order_id: str | None = None
    order_date: str | None = None  # YYYY/MM/DD or YYYY-MM-DD
    ship_by: str | None = None
    order_url: str | None = None
    lines: list[OrderLine] = field(default_factory=list)
    sku: str | None = None  # when single-target mails expose one SKU
    raw_text: str = ""


def _strip_html(html: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    s = re.sub(r"(?is)<br\s*/?>", "\n", s)
    s = re.sub(r"(?is)</(p|div|tr)>", "\n", s)
    s = re.sub(r"(?is)</td>", "\t", s)
    s = re.sub(r"(?is)<[^>]+>", " ", s)
    s = unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    return s.strip()


def _best_body(msg: email.message.Message) -> str:
    htmls: list[str] = []
    plains: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            try:
                content = part.get_content()
            except Exception:
                continue
            if not isinstance(content, str):
                continue
            if ctype == "text/html":
                htmls.append(_strip_html(content))
            elif ctype == "text/plain":
                plains.append(content)
    else:
        try:
            content = msg.get_content()
        except Exception:
            content = ""
        if msg.get_content_type() == "text/html" and isinstance(content, str):
            htmls.append(_strip_html(content))
        elif isinstance(content, str):
            plains.append(content)
    candidates = htmls + plains
    return max(candidates, key=len) if candidates else ""


def _decode_redirect_u(url: str) -> str | None:
    try:
        qs = parse_qs(urlparse(url).query)
        enc = (qs.get("u") or [""])[0]
        if not enc:
            return None
        pad = "=" * (-len(enc) % 4)
        return base64.urlsafe_b64decode(enc + pad).decode("utf-8", errors="replace")
    except Exception:
        return None


def _first_order_url(html_or_text: str) -> str | None:
    for m in re.finditer(
        r"https://sellercentral-japan\.amazon\.com/nms/sellermobile/redirect/[^\s\"'<>]+",
        html_or_text,
    ):
        raw = m.group(0).replace("&amp;", "&")
        dec = _decode_redirect_u(raw)
        if dec and "/order/" in dec:
            return dec
    return None


def _money_after(label: str, text: str) -> int | None:
    m = re.search(label + r"[^\d￥¥\n]{0,20}[￥¥]?\s*([0-9,]+)", text)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def _line_from_block(block: str, *, default_title: str = "") -> OrderLine | None:
    block = block[:800]
    title = default_title
    if not title:
        for raw in block.strip().splitlines():
            cand = raw.strip()
            if not cand:
                continue
            if _SKU_LABEL_RE.match(cand):
                continue
            if cand.startswith(("数量", "価格", "税金", "手数料", "Amazon", "売上", "付与")):
                continue
            if _MULTI_SOLD_HEADLINE_RE.search(cand):
                continue
            title = cand
            break
    sku_m = _SKU_LABEL_RE.search(block)
    sku = normalize_sku(sku_m.group(1) if sku_m else "")
    qty_m = re.search(r"数量\s*[:：]\s*(\d+)", block)
    price = _money_after(r"価格\s*[:：]", block)
    if price is None:
        price = _money_after(r"価格：", block)
    tax = _money_after(r"税金\s*[:：]", block)
    fee = _money_after(r"Amazon手数料\s*[:：]", block)
    if fee is None:
        fee = _money_after(r"手数料\s*[:：]", block)
    proceeds = _money_after(r"売上金\s*[:：]", block)
    pt_m = re.search(r"付与されたAmazonポイント\s*[:：]\s*(\d+)", block)
    points = int(pt_m.group(1)) if pt_m else None
    if points is None and price is not None:
        points = points_fallback(price)
    # Drop empty junk (headline-only / no sku & no money)
    if not sku and not title and price is None:
        return None
    if is_placeholder_sku(title) and not sku and price is None:
        return None
    if is_placeholder_sku(title):
        title = ""
    return OrderLine(
        sku=sku,
        title=title,
        qty=int(qty_m.group(1)) if qty_m else 1,
        price=price,
        tax=tax,
        fee=fee,
        points=points,
        proceeds=proceeds,
    )


def _parse_order_lines(text: str) -> list[OrderLine]:
    """One OrderLine per SKU / 商品 block (multi-item → multiple rows)."""
    lines: list[OrderLine] = []

    # Preferred: split on 商品： / 商品:
    parts = _PRODUCT_SPLIT_RE.split(text)
    if len(parts) > 1:
        for part in parts[1:]:
            cut = part
            for sep in ("出荷予定日", "注文番号", "注文日"):
                # keep ship_by on first item; trim subsequent headers from block window
                idx = cut.find(sep)
                if idx > 40:
                    cut = cut[:idx]
                    break
            line = _line_from_block(cut)
            if line:
                lines.append(line)
        if lines:
            return lines

    # Fallback: each SKU： starts a line (multi-item mails without 商品：)
    matches = list(_SKU_LABEL_RE.finditer(text))
    for i, m in enumerate(matches):
        sku = normalize_sku(m.group(1))
        if not sku:
            continue
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else start + 800
        window = text[max(0, start - 200) : end]
        # title: last non-empty line before SKU label in window
        before = window[: window.find(m.group(0))]
        title = ""
        for raw in reversed(before.splitlines()):
            cand = raw.strip()
            if not cand or _MULTI_SOLD_HEADLINE_RE.search(cand):
                continue
            if cand.startswith(("注文", "出荷", "数量", "価格", "税金", "手数料", "売上")):
                continue
            title = cand
            break
        line = _line_from_block(window[window.find(m.group(0)) :], default_title=title)
        if line:
            if not line.sku:
                line.sku = sku
            lines.append(line)
    return lines


def classify_subject(subject: str) -> str | None:
    s = subject or ""
    if "注文確定" in s:
        return "order"
    if "キャンセルリクエスト" in s or "キャンセルの依頼" in s:
        return "cancel_request"
    if "返品承認" in s:
        return "return_approved"
    if "マーケットプレイス保証" in s or "A-to-Z" in s or "保証による保護" in s:
        return "atoz"
    return None


def parse_eml_bytes(data: bytes) -> ParsedMail | None:
    msg = email.message_from_bytes(data, policy=policy.default)
    subject = str(msg.get("Subject") or "")
    kind = classify_subject(subject)
    if not kind:
        return None
    # Prefer raw html for URL extraction
    html_raw = ""
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            try:
                c = part.get_content()
                if isinstance(c, str) and len(c) > len(html_raw):
                    html_raw = c
            except Exception:
                pass
    text = _best_body(msg)
    order_ids = ORDER_ID_RE.findall(subject + "\n" + text)
    order_id = order_ids[0] if order_ids else None
    parsed = ParsedMail(kind=kind, subject=subject, order_id=order_id, raw_text=text)

    if kind == "order":
        od = re.search(
            r"注文日\s*[:：]\s*([0-9]{4}[/-][0-9]{1,2}[/-][0-9]{1,2})", text
        )
        sb = re.search(
            r"出荷予定日\s*[:：]\s*([0-9]{4}[/-][0-9]{1,2}[/-][0-9]{1,2})", text
        )
        parsed.order_date = od.group(1) if od else None
        parsed.ship_by = sb.group(1) if sb else None
        parsed.order_url = _first_order_url(html_raw or text)
        if not parsed.order_id:
            oid = re.search(r"注文番号\s*[:：]\s*(\d{3}-\d{7}-\d{7})", text)
            parsed.order_id = oid.group(1) if oid else None
        parsed.lines = _parse_order_lines(text)
        return parsed

    sku_m = _SKU_LABEL_RE.search(text)
    if sku_m:
        parsed.sku = normalize_sku(sku_m.group(1)) or None
    return parsed


def parse_eml_path(path: str | Path) -> ParsedMail | None:
    return parse_eml_bytes(Path(path).read_bytes())


def parse_eml_dir(directory: str | Path) -> list[ParsedMail]:
    root = Path(directory)
    out: list[ParsedMail] = []
    for p in sorted(root.glob("*.eml")):
        parsed = parse_eml_path(p)
        if parsed:
            out.append(parsed)
    return out


def to_dict(p: ParsedMail) -> dict[str, Any]:
    return {
        "kind": p.kind,
        "subject": p.subject,
        "order_id": p.order_id,
        "order_date": p.order_date,
        "ship_by": p.ship_by,
        "order_url": p.order_url,
        "sku": p.sku,
        "lines": [line.__dict__ for line in p.lines],
    }
