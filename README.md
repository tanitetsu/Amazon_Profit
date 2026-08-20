# Amazon 利益管理（Gmail → Google スプレッドシート）

OAuth / 本番サービス名: **`amazon-profit-viewer`**

設計の正本: [docs/architecture.md](docs/architecture.md) · 運用: [docs/operations.md](docs/operations.md) · Git: [docs/git-workflow.md](docs/git-workflow.md) · Cloud Agent: [docs/cloud-agent-environment.md](docs/cloud-agent-environment.md) · 統合: [docs/integration-ai-clipping.md](docs/integration-ai-clipping.md) · リポ境界: [docs/repo-boundaries.md](docs/repo-boundaries.md) · **本番前**: [docs/pre-production-checklist.md](docs/pre-production-checklist.md)

## 確定仕様（初回ユーザー）

| 項目 | 値 |
|---|---|
| 連携 Gmail | `asamiodaka@gmail.com` |
| スプレッドシート名 | `amazon-profit_asamiodaka_2026.xlsx` |
| 保存先アカウント | `26964u@gmail.com` |
| フォルダ名 | `User_Acounting`（スペルはこのまま） |

シート先頭に **ダッシュボード**、続けて **YYYY-MM** の月次注文データ。  
チェックボックスは **仕入 / 発送 / キャンセル / 完了**。状態値は `○` / `×` / `-` / `返品`。  
注文の反映は Gmail メール取込（Excel 一括投入は非目的）。

## 管理者 UI

```powershell
.\scripts\start-admin.ps1
```

- 新規追加: シート作成・共有 → AI_Cripping 名簿・`setting/user/{id}/`（quitted あれば復元／無ければ seed）→ Gmail 同意メール
- 同年次ファイルあり: **再利用**（共有・範囲保護の除去・名簿のみ。空にするのは `rebuild=True`）
- 削除: 共有解除・名簿除外・Gmail 連携解除。`setting/user/{id}/` → `setting/quitted-user/{id}/`（scraping/log・シートは残す。`26964u` 禁止）
- ポーリング: `scripts\poll_gmail_ingest.py --loop --interval-sec 300`

## セットアップ（要約）

1. Drive / Sheets / Gmail API、OAuth クライアント、`secrets/oauth_client.json`
2. `python scripts/oauth_operator.py`（`26964u@gmail.com`・gmail.send 含む）
3. 必要なら GCS パス移行: `python scripts/migrate_gcs_setting_user_prefix.py --delete-legacy-flat`

詳細は operations / pre-production-checklist。
