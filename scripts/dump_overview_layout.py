"""Dump live Overview sheet structure for comparison with app/schema + sheet_style."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.google_clients import load_operator_credentials, sheets_service  # noqa: E402
from app.schema import SUMMARY_SHEET, col_letter  # noqa: E402
from app.sheets_retry import execute_with_retry  # noqa: E402

SPREADSHEET_ID = "1uvx1PwTXueF1HY4J1Cv_IlKulqCjyvSE-UtP4oCFb5E"
MAX_ROW = 30
MAX_COL = 13  # M


def _a1(r: int, c: int) -> str:
    return f"{col_letter(c + 1)}{r + 1}"


def _rgb(c: dict | None) -> str | None:
    if not c:
        return None
    r = int(round(c.get("red", 0) * 255))
    g = int(round(c.get("green", 0) * 255))
    b = int(round(c.get("blue", 0) * 255))
    return f"#{r:02x}{g:02x}{b:02x}"


def _cell_fmt(cell: dict) -> dict:
    ef = cell.get("effectiveFormat") or cell.get("userEnteredFormat") or {}
    tf = ef.get("textFormat") or {}
    nf = ef.get("numberFormat") or {}
    bg = ef.get("backgroundColor") or {}
    return {
        "fontSize": tf.get("fontSize"),
        "bold": tf.get("bold"),
        "foreground": _rgb(tf.get("foregroundColor")),
        "background": _rgb(bg) if bg else None,
        "hAlign": ef.get("horizontalAlignment"),
        "vAlign": ef.get("verticalAlignment"),
        "numberFormat": nf if nf else None,
    }


def main() -> int:
    creds = load_operator_credentials()
    sheets = sheets_service(creds)

    meta = execute_with_retry(
        sheets.spreadsheets().get(
            spreadsheetId=SPREADSHEET_ID,
            fields=(
                "properties.title,"
                "sheets(properties(sheetId,title,gridProperties,tabColorStyle),"
                "merges,conditionalFormats)"
            ),
        ),
        label="dump.get",
    )

    overview = None
    for s in meta.get("sheets", []):
        if s["properties"]["title"] == SUMMARY_SHEET:
            overview = s
            break
    if overview is None:
        titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
        print(json.dumps({"error": f"{SUMMARY_SHEET!r} not found", "sheet_titles": titles}, ensure_ascii=False, indent=2))
        return 1

    props = overview["properties"]
    sheet_id = props["sheetId"]
    grid = props.get("gridProperties") or {}
    merges = overview.get("merges") or []
    cond = overview.get("conditionalFormats") or []

    # Column widths (0-based indices)
    col_meta = execute_with_retry(
        sheets.spreadsheets().get(
            spreadsheetId=SPREADSHEET_ID,
            ranges=[f"'{SUMMARY_SHEET}'!A1:{col_letter(MAX_COL)}{MAX_ROW}"],
            includeGridData=True,
            fields=(
                "sheets(data(rowData(values(effectiveValue,userEnteredValue,effectiveFormat)),"
                "columnMetadata(pixelSize),rowMetadata(pixelSize)))"
            ),
        ),
        label="dump.grid",
    )
    sheet_data = (col_meta.get("sheets") or [{}])[0]
    data_blocks = sheet_data.get("data") or [{}]
    block = data_blocks[0] if data_blocks else {}
    col_meta_list = block.get("columnMetadata") or []
    row_meta_list = block.get("rowMetadata") or []
    row_data = block.get("rowData") or []

    col_widths = []
    for i in range(MAX_COL):
        w = None
        if i < len(col_meta_list):
            w = col_meta_list[i].get("pixelSize")
        col_widths.append({"col": col_letter(i + 1), "index0": i, "pixelSize": w})

    row_heights = []
    for r in range(min(MAX_ROW, len(row_meta_list) if row_meta_list else MAX_ROW)):
        h = None
        if r < len(row_meta_list):
            h = row_meta_list[r].get("pixelSize")
        row_heights.append({"row": r + 1, "index0": r, "pixelSize": h})

    values: list[list] = []
    formats: dict[str, dict] = {}
    for r in range(min(MAX_ROW, len(row_data))):
        row_vals: list = []
        cells = (row_data[r].get("values") or []) if row_data else []
        for c in range(MAX_COL):
            cell = cells[c] if c < len(cells) else {}
            ev = cell.get("effectiveValue") or {}
            uv = cell.get("userEnteredValue") or {}
            if "stringValue" in ev:
                val = ev["stringValue"]
            elif "numberValue" in ev:
                val = ev["numberValue"]
            elif "boolValue" in ev:
                val = ev["boolValue"]
            elif "formulaValue" in ev:
                val = ev["formulaValue"]
            elif "stringValue" in uv:
                val = uv["stringValue"]
            elif "formulaValue" in uv:
                val = uv["formulaValue"]
            else:
                val = ""
            row_vals.append(val)
            key = _a1(r, c)
            fmt = _cell_fmt(cell)
            if any(v is not None for v in fmt.values()):
                formats[key] = fmt
        values.append(row_vals)

    # Key cells for font sample
    key_cells = [
        "A1", "A2", "A4", "B5", "C5", "D5", "E5", "F5", "G5", "H5", "I5",
        "B6", "C6", "H6", "A8", "A9", "B9", "B10", "C10",
    ]
    key_formats = {k: formats.get(k) for k in key_cells if k in formats}

    merge_report = []
    for m in merges:
        sr, er = m.get("startRowIndex", 0), m.get("endRowIndex", 0)
        sc, ec = m.get("startColumnIndex", 0), m.get("endColumnIndex", 0)
        merge_report.append(
            {
                "range": f"{col_letter(sc + 1)}{sr + 1}:{col_letter(ec)}{er}",
                "startRowIndex": sr,
                "endRowIndex": er,
                "startColumnIndex": sc,
                "endColumnIndex": ec,
            }
        )

    out = {
        "spreadsheet_id": SPREADSHEET_ID,
        "spreadsheet_title": meta.get("properties", {}).get("title"),
        "sheet_title": props.get("title"),
        "sheet_id": sheet_id,
        "gridProperties": grid,
        "tabColorStyle": props.get("tabColorStyle"),
        "column_widths": col_widths,
        "row_heights_1_to_30": row_heights,
        "merges": merge_report,
        "conditionalFormats_count": len(cond),
        "conditionalFormats": cond,
        "values_A1_M30": values,
        "key_cell_formats": key_formats,
        "all_cell_formats_with_content": {
            k: v
            for k, v in formats.items()
            if any(
                values[int(k[1:]) - 1][ord(k[0]) - 65] not in ("", None)
                for _ in [0]
                if k[0].isalpha()
            )
        },
    }

    # Simpler: formats for non-empty cells
    nonempty_formats = {}
    for r, row in enumerate(values):
        for c, val in enumerate(row):
            if val not in ("", None):
                key = _a1(r, c)
                if key in formats:
                    nonempty_formats[key] = {"value": val, **formats[key]}
    out["nonempty_cell_formats"] = nonempty_formats

    out_path = ROOT / "scripts" / "dump_overview_output.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
