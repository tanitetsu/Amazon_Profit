"""Template workbook ops: copy provision, month clone, dashboard month rows."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from app.google_clients import (
    copy_spreadsheet_in_folder,
    drive_service,
    find_spreadsheet_in_folder,
    load_operator_credentials,
    load_operator_oauth_credentials,
    resolve_operator_folder_id,
    retire_spreadsheet_for_overwrite,
    sheets_service,
    uses_adc_credentials,
)
from app.schema import (
    MONTH_TEMPLATE_SHEET,
    OVERVIEW_META_UPDATED_LABEL,
    OVERVIEW_METRIC_KPI_INDEX,
    OVERVIEW_METRIC_LABELS,
    OVERVIEW_MONTH_DATA_START_ROW,
    OVERVIEW_MONTH_SLOTS,
    OVERVIEW_NUM_COLS,
    SUMMARY_SHEET,
    TEMPLATE_SPREADSHEET_TITLE,
    col_letter,
    spreadsheet_title_from_gmail,
)
from app.sheet_builder import month_kpi_anchor_a1, period_from_months
from app.sheet_protection import apply_protections, share_editor
from app.sheet_style import LINE, WHITE, HERO, _all_borders, _paint
from app.sheets_retry import batch_update, execute_with_retry, values_batch_update
from app.users_store import load_users_config

_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


class WorkbookExistsError(Exception):
    """Same user+year workbook already on Drive; Admin must choose overwrite|keep."""

    def __init__(
        self,
        *,
        gmail: str,
        title: str,
        spreadsheet_id: str,
        url: str,
    ) -> None:
        self.gmail = gmail
        self.title = title
        self.spreadsheet_id = spreadsheet_id
        self.url = url
        super().__init__(f"workbook already exists: {title}")


class ProvisionError(Exception):
    """Provision failed after best-effort rollback of this attempt."""

    def __init__(
        self,
        message: str,
        *,
        rollback: dict[str, Any] | None = None,
    ) -> None:
        self.rollback = rollback or {}
        super().__init__(message)


def month_title(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def parse_month_title(title: str) -> tuple[int, int] | None:
    if not _MONTH_RE.match(title or ""):
        return None
    y, m = title.split("-")
    return int(y), int(m)


def iter_months_inclusive(a: str, b: str) -> list[str]:
    """Return YYYY-MM list from a..b inclusive in chronological order."""
    pa, pb = parse_month_title(a), parse_month_title(b)
    if not pa or not pb:
        return []
    (y0, m0), (y1, m1) = pa, pb
    if (y0, m0) > (y1, m1):
        (y0, m0), (y1, m1) = (y1, m1), (y0, m0)
    out: list[str] = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        out.append(month_title(y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def resolve_template_spreadsheet_id(drive=None, cfg: dict | None = None) -> str:
    cfg = cfg or load_users_config()
    tid = (cfg.get("template_spreadsheet_id") or "").strip()
    if tid:
        return tid
    drive = drive or drive_service()
    folder_id = resolve_operator_folder_id(drive, cfg["folder_name"])
    found = find_spreadsheet_in_folder(drive, TEMPLATE_SPREADSHEET_TITLE, folder_id)
    if not found:
        raise FileNotFoundError(
            f"template not found: {TEMPLATE_SPREADSHEET_TITLE} "
            "(run scripts/build_workbook_template.py)"
        )
    return found


def _sheet_map(sheets_api, spreadsheet_id: str) -> dict[str, dict[str, Any]]:
    meta = execute_with_retry(
        sheets_api.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets.properties(sheetId,title,index,hidden)",
        ),
        label="tmpl.meta",
    )
    return {s["properties"]["title"]: s["properties"] for s in meta.get("sheets", [])}


def hide_month_template_sheet(sheets_api, spreadsheet_id: str) -> bool:
    """
    Hide 月次テンプレート on a user workbook after template copy.

    Does not touch the live Drive template. Returns True if a hide was sent.
    """
    props = _sheet_map(sheets_api, spreadsheet_id).get(MONTH_TEMPLATE_SHEET)
    if not props or props.get("hidden"):
        return False
    batch_update(
        sheets_api,
        spreadsheet_id,
        [
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": props["sheetId"], "hidden": True},
                    "fields": "hidden",
                }
            }
        ],
        label="tmpl.hide.month_template",
    )
    return True


def list_month_sheets(sheets_api, spreadsheet_id: str) -> list[str]:
    titles = [t for t in _sheet_map(sheets_api, spreadsheet_id) if _MONTH_RE.match(t)]
    return sorted(titles, reverse=True)


def dashboard_meta_line(
    gmail: str,
    year: int,
    month_titles: list[str],
    *,
    now: datetime | None = None,
) -> str:
    title = spreadsheet_title_from_gmail(gmail, year)
    ps, pe = period_from_months(month_titles)
    start_s = ps.isoformat() if ps else "—"
    end_s = pe.isoformat() if pe else "—"
    ts = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
    return (
        f"ユーザー {gmail}   ·   ファイル {title}   ·   "
        f"期間 {start_s} 〜 {end_s}   ·   {OVERVIEW_META_UPDATED_LABEL} {ts}"
    )


def touch_last_auto_update(
    sheets_api,
    spreadsheet_id: str,
    *,
    gmail: str,
    year: int,
) -> None:
    months = list_month_sheets(sheets_api, spreadsheet_id)
    values_batch_update(
        sheets_api,
        spreadsheet_id,
        [
            {
                "range": f"'{SUMMARY_SHEET}'!A2",
                "values": [[dashboard_meta_line(gmail, year, months)]],
            }
        ],
        label="tmpl.meta.touch",
    )


def _month_row_values(month: str) -> list[Any]:
    return [f'="{month}"'] + [
        f"='{month}'!{month_kpi_anchor_a1(i)}" for i in OVERVIEW_METRIC_KPI_INDEX
    ]


def _write_dashboard_months_sorted(
    sheets_api, spreadsheet_id: str, months_desc: list[str]
) -> None:
    """Write month breakdown rows (descending) into fixed slots; clear the rest."""
    if len(months_desc) > OVERVIEW_MONTH_SLOTS:
        raise ValueError(f"more than {OVERVIEW_MONTH_SLOTS} months: {months_desc}")
    smap = _sheet_map(sheets_api, spreadsheet_id)
    dash_id = smap[SUMMARY_SHEET]["sheetId"]
    start = OVERVIEW_MONTH_DATA_START_ROW
    end = start + OVERVIEW_MONTH_SLOTS - 1
    grid: list[list[Any]] = []
    for i in range(OVERVIEW_MONTH_SLOTS):
        if i < len(months_desc):
            grid.append(_month_row_values(months_desc[i]))
        else:
            grid.append([""] * OVERVIEW_NUM_COLS)
    values_batch_update(
        sheets_api,
        spreadsheet_id,
        [{"range": f"'{SUMMARY_SHEET}'!A{start}", "values": grid}],
        label="tmpl.dash.months",
    )
    reqs: list[dict[str, Any]] = [
        _paint(
            dash_id,
            start - 1,
            end,
            0,
            OVERVIEW_NUM_COLS,
            None,
            borders={
                "top": {"style": "NONE"},
                "bottom": {"style": "NONE"},
                "left": {"style": "NONE"},
                "right": {"style": "NONE"},
            },
        )
    ]
    if months_desc:
        r0 = start - 1
        r1 = start - 1 + len(months_desc)
        reqs.append(
            _paint(
                dash_id,
                r0,
                r1,
                0,
                OVERVIEW_NUM_COLS,
                WHITE,
                textFormat={"foregroundColor": HERO, "fontSize": 11},
                horizontalAlignment="RIGHT",
                verticalAlignment="MIDDLE",
                borders=_all_borders(1, LINE),
            )
        )
    batch_update(sheets_api, spreadsheet_id, reqs, chunk_size=10, label="tmpl.dash.style")


def _reorder_tabs(sheets_api, spreadsheet_id: str, months_desc: list[str]) -> None:
    smap = _sheet_map(sheets_api, spreadsheet_id)
    desired = [SUMMARY_SHEET, *months_desc]
    if MONTH_TEMPLATE_SHEET in smap:
        desired.append(MONTH_TEMPLATE_SHEET)
    reqs = []
    for i, title in enumerate(desired):
        if title not in smap:
            continue
        reqs.append(
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": smap[title]["sheetId"], "index": i},
                    "fields": "index",
                }
            }
        )
    if reqs:
        batch_update(sheets_api, spreadsheet_id, reqs, chunk_size=20, label="tmpl.tabs")


def _clone_month_from_template(sheets_api, spreadsheet_id: str, month: str) -> None:
    smap = _sheet_map(sheets_api, spreadsheet_id)
    if month in smap:
        return
    if MONTH_TEMPLATE_SHEET not in smap:
        raise RuntimeError(f"{MONTH_TEMPLATE_SHEET} missing in {spreadsheet_id}")
    src_id = smap[MONTH_TEMPLATE_SHEET]["sheetId"]
    # Insert after dashboard (index 1)
    batch_update(
        sheets_api,
        spreadsheet_id,
        [
            {
                "duplicateSheet": {
                    "sourceSheetId": src_id,
                    "insertSheetIndex": 1,
                    "newSheetName": month,
                }
            }
        ],
        label="tmpl.duplicate",
    )
    # User books keep 月次テンプレート hidden; duplicate inherits hidden → show month tab
    smap2 = _sheet_map(sheets_api, spreadsheet_id)
    sheet_id = smap2[month]["sheetId"]
    if smap2[month].get("hidden"):
        batch_update(
            sheets_api,
            spreadsheet_id,
            [
                {
                    "updateSheetProperties": {
                        "properties": {"sheetId": sheet_id, "hidden": False},
                        "fields": "hidden",
                    }
                }
            ],
            label="tmpl.unhide",
        )
    values_batch_update(
        sheets_api,
        spreadsheet_id,
        [{"range": f"'{month}'!A1", "values": [[month]]}],
        label="tmpl.month.title",
    )


def ensure_months_for_order(
    sheets_api,
    spreadsheet_id: str,
    target_month: str,
    *,
    gmail: str,
    year: int,
) -> list[str]:
    """
    Ensure target month sheet exists. If not contiguous with the newest month,
    also create intermediate months. Dashboard: write then reorder descending.
    Touches 最終自動更新.
    """
    if not parse_month_title(target_month):
        raise ValueError(f"invalid month: {target_month}")
    existing = list_month_sheets(sheets_api, spreadsheet_id)
    if not existing:
        needed = [target_month]
    else:
        newest = existing[0]
        needed = [
            m
            for m in iter_months_inclusive(newest, target_month)
            if m not in existing
        ]

    if not needed and target_month in existing:
        # Keep / re-provision path: months already contiguous — skip clone,
        # dashboard rewrite, and tab reorder (those dominate Sheets latency).
        touch_last_auto_update(sheets_api, spreadsheet_id, gmail=gmail, year=year)
        return existing

    for m in needed:
        _clone_month_from_template(sheets_api, spreadsheet_id, m)

    all_months = list_month_sheets(sheets_api, spreadsheet_id)
    if target_month not in all_months:
        all_months = sorted({*all_months, target_month}, reverse=True)
    _write_dashboard_months_sorted(sheets_api, spreadsheet_id, all_months)
    _reorder_tabs(sheets_api, spreadsheet_id, all_months)
    touch_last_auto_update(sheets_api, spreadsheet_id, gmail=gmail, year=year)
    return all_months


def next_data_row(sheets_api, spreadsheet_id: str, month: str) -> int:
    """First empty detail row for APPEND."""
    from app.schema import DATA_START_ROW, FORMULA_END_ROW
    from app.sheets_retry import execute_with_retry

    data = (
        execute_with_retry(
            sheets_api.spreadsheets()
            .values()
            .get(
                spreadsheetId=spreadsheet_id,
                range=f"'{month}'!A{DATA_START_ROW}:A{FORMULA_END_ROW}",
            ),
            label=f"next_data_row:{month}",
        ).get("values")
        or []
    )
    for i, row in enumerate(data):
        if not row or not str(row[0]).strip():
            return DATA_START_ROW + i
    return DATA_START_ROW + len(data)



def _rollback_provision_attempt(
    drive,
    *,
    gmail: str,
    title: str,
    spreadsheet_id: str | None,
    created_this_run: bool,
    roster_touched: bool,
    roster_was_new: bool,
    iap_touched: bool,
) -> dict[str, Any]:
    """
    Best-effort undo for a failed provision attempt.

    - New sheet this run: revoke IAP → remove roster (if new) → unshare → retire file
    - Keep/reuse existing sheet: revoke IAP / remove roster only when this run added them;
      never delete the workbook (may already hold order data)
    """
    out: dict[str, Any] = {
        "gmail": gmail,
        "title": title,
        "spreadsheet_id": spreadsheet_id,
        "created_this_run": created_this_run,
        "iap_revoked": False,
        "roster_removed": False,
        "unshared": False,
        "sheet_retired": None,
        "errors": [],
    }

    if iap_touched:
        try:
            from app.iap_access import revoke_iap_access

            revoke_iap_access(gmail)
            out["iap_revoked"] = True
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"iap_revoke: {exc}")

    if roster_touched and (roster_was_new or created_this_run):
        try:
            from app.clipping_roster import remove_clipping_user

            remove_clipping_user(gmail)
            out["roster_removed"] = True
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"roster_remove: {exc}")

    if spreadsheet_id and (created_this_run or roster_touched or iap_touched):
        should_unshare = created_this_run or (roster_touched and roster_was_new)
        if should_unshare:
            try:
                from app.sheet_protection import unshare_user

                out["unshared"] = bool(unshare_user(drive, spreadsheet_id, gmail))
            except Exception as exc:  # noqa: BLE001
                out["errors"].append(f"unshare: {exc}")

    if created_this_run and spreadsheet_id:
        try:
            out["sheet_retired"] = retire_spreadsheet_for_overwrite(
                drive, spreadsheet_id, title
            )
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"retire: {exc}")

    print(f"provision rollback: {out}", flush=True)
    return out


def provision_from_template(
    gmail: str,
    note: str = "",
    *,
    role: str = "Normal",
    year: int | None = None,
    rebuild: bool = False,
    existing_file_action: str | None = None,
) -> dict[str, Any]:
    """
    Create yearly workbook by copying the Drive template.

    existing_file_action (when same user+year file already exists):
      - None: raise WorkbookExistsError (Admin UI retries with keep = reuse)
      - "overwrite": retire existing then copy template (API escape hatch)
      - "keep": reuse file; protections + share + roster only

    Roster / IAP failures raise after rolling back this attempt (new sheets are retired).
    """
    from app.ai_roles import normalize_app_role
    from app.clipping_roster import load_role_map, upsert_clipping_user
    from app.iap_access import grant_iap_access
    from app.schema import user_id_from_gmail

    gmail = gmail.strip()
    app_role = normalize_app_role(role)
    y = year if year is not None else date.today().year
    cfg = load_users_config()
    # After template copy, protected-range editors often keep only the owner.
    # SA then cannot values.batchUpdate the dashboard until apply_protections.
    # Provision writes therefore use operator OAuth on Cloud Run (ADC).
    if uses_adc_credentials():
        creds = load_operator_oauth_credentials()
    else:
        creds = load_operator_credentials()
    drive = drive_service(creds)
    sheets = sheets_service(creds)
    folder_id = resolve_operator_folder_id(drive, cfg["folder_name"])
    title = spreadsheet_title_from_gmail(gmail, y)
    template_id = resolve_template_spreadsheet_id(drive, cfg)

    existing = find_spreadsheet_in_folder(drive, title, folder_id)
    action = (existing_file_action or "").strip().lower()
    if rebuild:
        action = "overwrite"

    created_new = existing is None
    skipped_create = False
    rebuilt = False
    retire_mode: str | None = None

    if existing and not action:
        url = f"https://docs.google.com/spreadsheets/d/{existing}/edit"
        raise WorkbookExistsError(
            gmail=gmail, title=title, spreadsheet_id=existing, url=url
        )

    if existing and action == "overwrite":
        retire_mode = retire_spreadsheet_for_overwrite(drive, existing, title)
        existing = None
        created_new = True
        rebuilt = True
    elif existing and action == "keep":
        skipped_create = True
        created_new = False
    elif existing:
        raise ValueError(
            f"unknown existing_file_action: {existing_file_action!r} "
            "(want overwrite|keep)"
        )

    if existing:
        spreadsheet_id = existing
        initialized = False
        created_this_run = False
    else:
        spreadsheet_id = copy_spreadsheet_in_folder(
            drive, template_id, title, folder_id
        )
        # Hide seed sheet on the user book only (never mutate the live template)
        hide_month_template_sheet(sheets, spreadsheet_id)
        initialized = True
        created_this_run = True

    uid = user_id_from_gmail(gmail)
    roster_was_new = uid not in load_role_map()
    roster_touched = False
    iap_touched = False
    clipping: dict[str, Any] | None = None
    iap: dict[str, Any] | None = None
    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"

    try:
        import time as _time

        t0 = _time.monotonic()
        current = month_title(y, date.today().month)
        ensure_months_for_order(
            sheets, spreadsheet_id, current, gmail=gmail, year=y
        )
        print(
            f"provision: ensure_months {(_time.monotonic()-t0):.1f}s "
            f"keep={skipped_create}",
            flush=True,
        )
        t1 = _time.monotonic()
        apply_protections(
            sheets,
            spreadsheet_id,
            role=app_role,
            skip_if_present=skipped_create,
        )
        print(
            f"provision: apply_protections {(_time.monotonic()-t1):.1f}s "
            f"skip_if_present={skipped_create}",
            flush=True,
        )

        # Cancel☑→状態: テンプレ copy で bound onEdit を継承。API 再配備はしない
        # （simple onEdit の登録が壊れることがある）。

        t2 = _time.monotonic()
        share_editor(
            drive,
            spreadsheet_id,
            gmail,
            send_notification=True,
            email_message=(
                "Amazon利益管理シートを共有しました。\n"
                f"ファイル名: {title}\n"
                f"リンク: {url}\n\n"
                "編集できるのは青い列（仕入金 / 諸費用 / 発送日 / 仕入 / 発送 / "
                "キャンセル / 完了 / コメント）です。"
                "金額・注文情報は自動更新のため保護されています。"
            ),
        )
        print(f"provision: share_editor {(_time.monotonic()-t2):.1f}s", flush=True)

        t3 = _time.monotonic()
        clipping = upsert_clipping_user(gmail, app_role)
        roster_touched = True
        if not clipping.get("confirmed_in_roster"):
            raise RuntimeError(
                f"roster upsert not confirmed for {uid} after write"
            )
        print(
            f"provision: roster+seed {(_time.monotonic()-t3):.1f}s role={app_role}",
            flush=True,
        )

        t4 = _time.monotonic()
        iap_touched = True  # may partially apply before raising
        iap = grant_iap_access(gmail)
        print(f"provision: iap {(_time.monotonic()-t4):.1f}s", flush=True)
    except WorkbookExistsError:
        raise
    except Exception as exc:
        rollback = _rollback_provision_attempt(
            drive,
            gmail=gmail,
            title=title,
            spreadsheet_id=spreadsheet_id,
            created_this_run=created_this_run,
            roster_touched=roster_touched,
            roster_was_new=roster_was_new,
            iap_touched=iap_touched,
        )
        raise ProvisionError(
            f"provision failed for {gmail}: {exc}",
            rollback=rollback,
        ) from exc

    return {
        "gmail": gmail,
        "title": title,
        "spreadsheet_id": spreadsheet_id,
        "url": url,
        "role": app_role,
        "created_new": created_new,
        "initialized": initialized,
        "rebuilt": rebuilt,
        "skipped_create": skipped_create,
        "retire_mode": retire_mode,
        "shared": True,
        "notified": True,
        "template_id": template_id,
        "clipping": clipping,
        "clipping_error": None,
        "iap": iap,
        "iap_error": None,
    }



def annual_sum_formulas() -> list[Any]:
    """Dashboard row-6: SUM over 12 month slots; rate = (利益−諸費用)/売上金."""
    from app.schema import OVERVIEW_KPI_VALUE_ROW

    start = OVERVIEW_MONTH_DATA_START_ROW
    end = start + OVERVIEW_MONTH_SLOTS - 1
    rate_i = OVERVIEW_METRIC_LABELS.index("利益率")
    proceeds_i = OVERVIEW_METRIC_LABELS.index("売上金")
    extra_i = OVERVIEW_METRIC_LABELS.index("諸費用")
    profit_i = OVERVIEW_METRIC_LABELS.index("利益")
    vr = OVERVIEW_KPI_VALUE_ROW
    annual: list[Any] = [""]
    for i in range(len(OVERVIEW_METRIC_LABELS)):
        letter = col_letter(i + 2)
        if i == rate_i:
            profit_a1 = f"{col_letter(profit_i + 2)}{vr}"
            extra_a1 = f"{col_letter(extra_i + 2)}{vr}"
            proceeds_a1 = f"{col_letter(proceeds_i + 2)}{vr}"
            annual.append(
                f'=IF({proceeds_a1}=0,"",({profit_a1}-{extra_a1})/{proceeds_a1})'
            )
        else:
            annual.append(f"=SUM({letter}{start}:{letter}{end})")
    return annual
