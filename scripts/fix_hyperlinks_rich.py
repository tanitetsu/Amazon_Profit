"""Convert order/title cells to Insert-link style (hover preview / thumbnails)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.google_clients import sheets_service  # noqa: E402
from app.schema import COL, DATA_START_ROW, DETAIL_SPANS, col_letter  # noqa: E402
from app.sheet_links import apply_rich_links, order_title_rich_links  # noqa: E402

_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
SID = "1pPP_vobhkK0QzEIxE5Uht578T_Aqp9DJx1Ormya8C2k"


def main() -> int:
    s = sheets_service()
    meta = (
        s.spreadsheets()
        .get(spreadsheetId=SID, fields="sheets.properties(sheetId,title)")
        .execute()
    )
    oid_l = col_letter(COL["order_id"])
    title_l = col_letter(COL["title"])
    sku_l = col_letter(COL["sku"])
    total = 0
    for sh in meta["sheets"]:
        m = sh["properties"]["title"]
        if not _MONTH_RE.match(m):
            continue
        sheet_id = sh["properties"]["sheetId"]
        resp = (
            s.spreadsheets()
            .values()
            .batchGet(
                spreadsheetId=SID,
                ranges=[
                    f"'{m}'!{oid_l}{DATA_START_ROW}:{oid_l}",
                    f"'{m}'!{title_l}{DATA_START_ROW}:{title_l}",
                    f"'{m}'!{sku_l}{DATA_START_ROW}:{sku_l}",
                ],
                valueRenderOption="FORMATTED_VALUE",
            )
            .execute()
        )
        oids = resp["valueRanges"][0].get("values") or []
        titles = resp["valueRanges"][1].get("values") or []
        skus = resp["valueRanges"][2].get("values") or []
        n = max(len(oids), len(titles), len(skus))
        rows: list[tuple[int, str | None, str | None, str | None]] = []
        for i in range(n):
            oid = oids[i][0] if i < len(oids) and oids[i] else ""
            title = titles[i][0] if i < len(titles) and titles[i] else ""
            sku = skus[i][0] if i < len(skus) and skus[i] else ""
            if not str(oid).strip() and not str(title).strip():
                continue
            rows.append((DATA_START_ROW + i, oid or None, title or None, sku or None))
        links = order_title_rich_links(
            rows,
            order_col_0=DETAIL_SPANS["order_id"][0],
            title_col_0=DETAIL_SPANS["title"][0],
        )
        print(f"{m}: rows={len(rows)} links={len(links)}", flush=True)
        total += apply_rich_links(
            s, SID, sheet_id, links, label=f"rich.{m}"
        )
    print(f"done total_link_cells={total}")
    # sample
    sample = f"'2026-08'!{oid_l}{DATA_START_ROW}"
    cell = (
        s.spreadsheets()
        .get(
            spreadsheetId=SID,
            ranges=[sample],
            includeGridData=True,
            fields=(
                "sheets.data.rowData.values("
                "userEnteredValue,hyperlink,textFormatRuns,formattedValue)"
            ),
        )
        .execute()["sheets"][0]["data"][0]["rowData"][0]["values"][0]
    )
    print(
        "sample",
        "formula" if (cell.get("userEnteredValue") or {}).get("formulaValue") else "rich",
        "hyperlink=",
        (cell.get("hyperlink") or "")[:60],
        "runs=",
        bool(cell.get("textFormatRuns")),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
