"""Send Gmail-link consent mail from the operator account (Gmail API)."""

from __future__ import annotations

import base64
from email.mime.text import MIMEText
from typing import Any

from googleapiclient.discovery import build

from app.gmail_oauth import consent_start_url, public_base_url
from app.google_clients import load_operator_oauth_credentials
from app.sheets_retry import execute_with_retry
from app.users_store import load_users_config


def send_gmail_consent_email(
    to_gmail: str,
    *,
    sheet_url: str | None = None,
    sheet_title: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """
    Email the end user a link to grant gmail.readonly.
    Uses operator user OAuth (26964u…), not the Cloud Run service account.
    """
    to_gmail = to_gmail.strip()
    base = base_url or public_base_url()
    link = consent_start_url(to_gmail, base_url=base)
    cfg = load_users_config()
    operator = (cfg.get("operator_drive_email") or "").strip() or "運営"

    lines = [
        "Amazon利益管理（amazon-profit-viewer）への Gmail 連携のお願いです。",
        "",
        "出品関連の Amazon 通知メールを読み取り、利益管理シートへ自動反映します。",
        "次のリンクを開き、Google アカウント（このメールアドレス）で「許可」してください。",
        "",
        link,
        "",
        "※ リンクの有効期限は 7 日です。",
        "※ 許可後は定期的にメールを取り込みます（約 5 分間隔）。",
        "※ 読み取り専用です。メールの削除・送信は行いません。",
    ]
    if sheet_title or sheet_url:
        lines.extend(["", f"シート: {sheet_title or ''}", sheet_url or ""])
    lines.extend(["", f"— {operator} / amazon-profit-viewer"])

    body = "\n".join(lines)
    message = MIMEText(body, _charset="utf-8")
    message["to"] = to_gmail
    message["from"] = operator
    message["subject"] = "【amazon-profit-viewer】Gmail 連携のお願い"

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    creds = load_operator_oauth_credentials()
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    sent = execute_with_retry(
        service.users().messages().send(userId="me", body={"raw": raw}),
        label="gmail.messages.send",
    )
    return {
        "to": to_gmail,
        "consent_url": link,
        "message_id": sent.get("id"),
        "from": operator,
        "base_url": base,
    }
