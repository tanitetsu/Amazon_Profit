import json, sys
from pathlib import Path
sys.path.insert(0, r"E:\Web Projects\amazon-profit-mail")
from app.apps_script_deploy import SCRIPT_META_KEY, _read_script_meta, script_service
from app.google_clients import drive_service, load_operator_credentials, resolve_operator_folder_id, sheets_service
from app.schema import TEMPLATE_SPREADSHEET_TITLE, apps_script_cancel_onedit_source
from app.sheets_retry import execute_with_retry
from app.users_store import load_users_config

cfg = load_users_config()
creds = load_operator_credentials()
drive = drive_service(creds)
sheets = sheets_service(creds)
svc = script_service(creds)
folder = resolve_operator_folder_id(drive, cfg["folder_name"])
print("folder", folder)
print("template_spreadsheet_id", cfg["template_spreadsheet_id"])

for pattern_label, q in [
    ("clean_tmp_in_folder", f"'{folder}' in parents and name contains 'clean.tmp' and trashed = false"),
    ("retired_in_folder", f"'{folder}' in parents and name contains '.retired.' and trashed = false"),
    ("clean_any", "name contains 'clean.tmp' and trashed = false"),
    ("tracaude_any", "name contains 'tracaude' and trashed = false"),
]:
    resp = execute_with_retry(drive.files().list(q=q, fields="files(id,name,trashed,modifiedTime,parents)", pageSize=50), label=pattern_label)
    files = resp.get("files") or []
    print(pattern_label, len(files))
    for f in files:
        print(" ", json.dumps(f, ensure_ascii=False))

books = [
    ("TEMPLATE", cfg["template_spreadsheet_id"]),
]
expect = apps_script_cancel_onedit_source().strip()
print("--- template script ---")
for label, bid in books:
    name = execute_with_retry(drive.files().get(fileId=bid, fields="id,name,trashed"), label="get").get("name")
    sid, mid = _read_script_meta(sheets, bid)
    row = {"label": label, "name": name, "id": bid, "meta_script": sid, "meta_id": mid}
    if sid:
        proj = execute_with_retry(svc.projects().get(scriptId=sid), label="p")
        content = execute_with_retry(svc.projects().getContent(scriptId=sid), label="c")
        files = content.get("files") or []
        js_files = [f for f in files if f.get("type") == "SERVER_JS"]
        js = next((f.get("source") or "" for f in js_files), "")
        row.update({
            "title": proj.get("title"),
            "parentId": proj.get("parentId"),
            "createTime": proj.get("createTime"),
            "updateTime": proj.get("updateTime"),
            "server_js_count": len(js_files),
            "file_names": [f.get("name") for f in files],
            "matches": js.strip() == expect,
            "retired_stub": "retired duplicate" in js,
            "has_onEdit": "function onEdit" in js,
            "js_len": len(js),
        })
    print(json.dumps(row, ensure_ascii=False))
print("--- expect snippet ---")
print(expect[:120].replace("\n","\\n"))
