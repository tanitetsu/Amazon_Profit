---
name: start-admin
description: >-
  amazon-profit-mail の管理者 UI（Flask / ポート 5055）を起動・再起動する。
  Admin 起動、管理者コンソール再起動、5055、start-admin、ユーザー一覧 UI を開く、とき使う。
disable-model-invocation: true
---

# 管理者 UI の起動・再起動

## 目的

ローカル管理者コンソールを `http://127.0.0.1:5055/` で起動する。**再起動も同じコマンド**（ポート 5055 の既存プロセスを止めてから起動し直す）。

## 手順（起動 / 再起動 共通）

1. リポジトリ直下（`amazon-profit-mail`）で作業する
2. `secrets/` の**中身は読まない**（オペレータ OAuth トークンのパス有無だけ）
3. 次で起動または再起動する:

### Agent から（推奨）

長時間プロセスなのでバックグラウンド起動し、同期チェックの入力待ちを避ける:

```powershell
$env:START_ADMIN_NO_PAUSE = "1"
.\scripts\start-admin.ps1
```

- `required_permissions`: `["all"]`（ポート解放・venv）
- `block_until_ms`: `0`（即バックグラウンド）
- 出力に `Running on` / `http://127.0.0.1:5055` が出るまで待つ
- スクリプトが準備完了後に **Chrome で自動オープン**する（無ければ既定ブラウザ）
- URL を案内する
- 起動時に `check-git-sync.ps1` が走る。`START_ADMIN_NO_PAUSE=1` のため警告のみ（入力待ちしない）

### ユーザーが手動で

```powershell
.\scripts\start-admin.ps1
```

## 再起動について

- **追加フラグは不要**。起動中でもう一度 `start-admin.ps1`（または `.bat`）を実行すれば再起動になる
- スクリプトがポート **5055** 使用中ならリスナーを止めてから起動する
- `app.py` / `static/` / `templates/` / `app/provision.py` などの変更反映は**再起動が必要**
- ポート解放に失敗したら、既存コンソールで Ctrl+C してから再実行

## 想定される挙動

- 起動時に `check-git-sync.ps1` が GitHub の `main` とのズレを確認する。手動起動では遅れていれば pull を尋ね、断っても Enter 待ち。`START_ADMIN_NO_PAUSE=1` のときは警告のみ。`SKIP_GIT_SYNC_CHECK=1` で省略可
- ポート **5055** 使用中 → リスナーを止めて再起動する

## 起動後の確認

- 起動時に **Chrome** で `http://127.0.0.1:5055/` が開く（専用プロファイル `.chrome-admin-profile`）
- 管理者コンソール・ユーザー一覧・「シートを開く」が出る
- 新規追加・削除（共有解除）が使える

## 禁止

- APIキーや `secrets/` の中身をチャットに貼らない
- 本番公開（`0.0.0.0` バインド）を勝手にしない（現状は `127.0.0.1` のみ）
