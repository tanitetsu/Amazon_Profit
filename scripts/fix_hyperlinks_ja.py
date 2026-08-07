"""Rewrite order/title HYPERLINK formulas with ja_JP ';' separators."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.google_clients import sheets_service  # noqa: E402
from app.schema import COL, DATA_START_ROW, col_letter  # noqa: E402
from app.sheet_links import order_id_cell, title_cell  # noqa: E402
from app.sheets_retry import values_batch_update  # noqa: E402

_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
SID = "1pPP_vobhkK0QzEIxE5Uht578T_Aqp9DJx1Ormya8C2k"


def main() -> int:
    s = sheets_service()
    meta = (
        s.spreadsheets()
        .get(spreadsheetId=SID, fields="sheets.properties(title)")
        .execute()
    )
    months = [
        x["properties"]["title"]
        for x in meta["sheets"]
        if _MONTH_RE.match(x["properties"]["title"])
    ]
    oid_l = col_letter(COL["order_id"])
    title_l = col_letter(COL["title"])
    sku_l = col_letter(COL["sku"])

    data: list[dict] = []
    n_order = n_title = 0
    for m in months:
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
        for i in range(n):
            row = DATA_START_ROW + i
            oid = oids[i][0] if i < len(oids) and oids[i] else ""
            title = titles[i][0] if i < len(titles) and titles[i] else ""
            sku = skus[i][0] if i < len(skus) and skus[i] else ""
            if not str(oid).strip() and not str(title).strip():
                continue
            new_oid = order_id_cell(str(oid).strip() or None)
            new_title = title_cell(str(title) if title else None, str(sku) if sku else None)
            if new_oid.startswith("=HYPERLINK"):
                data.append({"range": f"'{m}'!{oid_l}{row}", "values": [[new_oid]]})
                n_order += 1
            if new_title.startswith("=HYPERLINK"):
                data.append({"range": f"'{m}'!{title_l}{row}", "values": [[new_title]]})
                n_title += 1

    print(f"writing order_links={n_order} title_links={n_title} cells={len(data)}")
    # chunk
    chunk = 200
    for i in range(0, len(data), chunk):
        values_batch_update(
            s, SID, data[i : i + chunk], label=f"fix.links.{i // chunk}"
        )
        print(f"  chunk {i // chunk + 1}", flush=True)

    # verify sample
    sample = f"'{months[0]}'!{oid_l}{DATA_START_ROW}"
    gd = (
        s.spreadsheets()
        .get(
            spreadsheetId=SID,
            ranges=[sample],
            includeGridData=True,
            fields="sheets.data.rowData.values(hyperlink,formattedValue)",
        )
        .execute()
    )
    cell = gd["sheets"][0]["data"][0]["rowData"][0]["values"][0]
    print("sample hyperlink", cell.get("hyperlink"), "label", cell.get("formattedValue"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
