# Cloud Agent 開発環境（案 B）

正本。コード修正は **Cloud Agent**。自宅 PC のローカル Agent は **手動操作の確認**（管理画面・Chrome・シート見た目）程度。本番 Cloud Run デプロイは **依頼があれば** Cloud Agent の `scripts/deploy-admin.sh`（PC は従来の `deploy-admin.ps1` も可）。

## 問題と抑え方

| 問題 | 抑え方 |
|---|---|
| Agent が全ユーザーシートを書き換えられる | ライブ Drive／Apps Script は **依頼があるときだけ**（従来ルール）。依頼なしの再ビルド・再配備・表示切替はしない |
| 認証 JSON がリポやチャットに漏れる | 中身はコミットしない・読まない・貼らない。Cursor Environment の **secrets** と GCS URI だけ |
| PC に未コミットの直しが残る | PC では原則コードを直さない。残ったら stash / commit してから Agent を出す |
| Cloud Agent に GCP SA は付かない | **`ADMIN_USE_ADC` は付けない**。Drive は運営 OAuth（GCS の `OPERATOR_TOKEN_GCS_URI`） |
| デプロイで本番秘密を回してしまう | `deploy-admin.sh` は GCS に既にある invite / mail-poll 秘密を**作らない・上書きしない**。最新 `origin/main` かつ作業ツリーがきれいなときだけ |

反対意見: 認証をクラウドに置くと、誤操作の被害は PC 実行より大きい。その代わりスマホからも移行・取込確認まで完結できる。

## 役割分担

```text
【日常の修正】
  スマホ / PC の Cursor → Cloud Agent → 枝 → PR → main

【PC ローカル Agent】
  管理画面の手動クリック確認、Chrome での見た目
  同じファイルを Agent 作業中にローカルで直さない

【デプロイ】
  ユーザーが明示したときだけ。最新 origin/main から1本。
  Cloud Agent: ./scripts/deploy-admin.sh
  PC: scripts\\deploy-admin.ps1
```

## Cursor Environment に載せるもの

リポの `.cursor/environment.json` は **依存関係の install だけ**。秘密情報はここに書かない。

ダッシュボードの Environment **secrets / 環境変数**（値はチャットに貼らない）:

| 名前 | 用途 |
|---|---|
| `AIC_GCS_CREDENTIALS` | GCS 用 SA。**ファイルパス**でも、Cursor secrets に貼った **JSON 本文**（`{` で始まる）でも可。本文のときは一時ファイルへ書いてから使う（名簿・`app_config`・Gmail トークン読み） |
| `GOOGLE_APPLICATION_CREDENTIALS` | 上と同じ（パスまたは JSON 本文）。`AIC_GCS_CREDENTIALS` 優先 |
| `APP_CONFIG_GCS_URI` | 運営設定 `gs://…/app_config.json`（`USERS_CONFIG_GCS_URI` でも可） |
| `OPERATOR_TOKEN_GCS_URI` | 運営 OAuth JSON（Drive / Sheets / gmail.send） |
| `OAUTH_CLIENT_GCS_URI` | OAuth クライアント JSON（トークン更新に使う） |
| `GCP_DEPLOY_CREDENTIALS` | （任意）Cloud Run デプロイ専用 SA。パスまたは JSON 本文。未設定なら `AIC_GCS_CREDENTIALS` を使う |

**付けない**

- `ADMIN_USE_ADC=1`（Agent にランタイム SA が無い。付けると Drive が ADC になり失敗する）
- `secrets/` 配下の実ファイルをリポへコピーすること

Cloud Run 本番はこれまでどおり `ADMIN_USE_ADC=1` + ランタイム SA。Agent と本番で認証の足は分ける。

## 運営が一度だけやること

1. Cursor でこのリポの Cloud Agent Environment を作る（未作成なら）
2. 上表の変数を Environment secrets に入れる（Cloud Run と同じ GCS URI。SA はパスか JSON 本文。中身はリポに置かない・チャットに貼らない）
3. 以降の Agent はその Environment から起動する
4. 自宅 PC の作業コピーはきれいにする（未コミットを残さない）

この文書を書いた時点の Agent 実行には Environment が未接続だった。変数を入れた **次の** Agent から Drive / Gmail に届く。

## 動作確認（Agent 側・値は出さない）

Environment 接続後、中身を print せず:

- `APP_CONFIG_GCS_URI` / `OPERATOR_TOKEN_GCS_URI` が `gs://` で始まっている
- `load_users_config()` が例外なく返る
- `load_operator_credentials()` が例外なく返る

失敗したら URI と SA の権限（該当バケット読取）を確認する。JSON 本文はログに出さない。

## Cloud Agent からの本番デプロイ

依頼があるときだけ。自動では出さない。`ADMIN_USE_ADC` は **Agent プロセスには付けない**（付けるのは Cloud Run 側の既存設定）。

```bash
./scripts/deploy-admin.sh --dry-run
./scripts/deploy-admin.sh --skip-iap
```

既定は最新 `origin/main` かつ未コミットなし。枝のまま出すのは `--allow-non-main`（明示依頼のみ）。

デプロイ SA（`GCP_DEPLOY_CREDENTIALS` または `AIC_GCS_CREDENTIALS` の `client_email`）に、プロジェクト `positive-design-480606-c7` で次が必要（未付与なら PC のオーナーで一度だけ）:

- `roles/run.admin`
- `roles/cloudbuild.builds.editor`
- `roles/artifactregistry.writer`
- `roles/iam.serviceAccountUser`（対象: `amazon-profit-admin@…`）
- `roles/storage.objectAdmin`（運営バケット。名簿用で付いていることが多い）
- `roles/serviceusage.serviceUsageConsumer`

IAP の初回有効化はプロジェクト IAM が要ることがある。再デプロイは `--skip-iap` でよい（IAP は本番済み）。

## GitHub Actions からの本番反映（クラウド完結・auto_clipper と同方式）

PC や Cloud Agent VM に依存せず、**GitHub からクラウドで本番反映**する。中身は同じ `scripts/deploy-admin.sh`（gcloud 自動導入・SA 有効化・API/AR/GCS/Cloud Run/IAP まで自己完結）を GitHub ホストランナーで実行するだけ。

- ワークフロー: `.github/workflows/deploy-admin.yml`
- **トリガーは手動のみ**（`workflow_dispatch`）。`main` への push では自動デプロイしない（本リポの「デプロイは明示時だけ」方針を守るため）。GitHub の Actions 画面で対象ワークフロー → **Run workflow** を押したときが「明示の依頼」になる
- 既定は `main` からのみ。別枝で出すときだけ `allow_non_main` を明示。CI 内では脚本の git 同期チェックは `--skip-git-sync-check` で無効化し、枝ガードで代替する
- 入力: `dry_run` / `skip_iap`（既定 true・再デプロイ向け）/ `skip_oauth` / `skip_build` / `allow_non_main` / `project_id`

### 一度だけの準備（運営）

1. GitHub リポ **Settings → Secrets and variables → Actions** に `GCP_DEPLOY_CREDENTIALS` を追加（デプロイ用 SA の **JSON 本文**。チャットには貼らない）。ワークフローは受け取った JSON を一時ファイル（`chmod 600`）に書き出して脚本へパス渡しする
2. その SA に本節冒頭の IAM ロール（`run.admin` / `cloudbuild.builds.editor` / `artifactregistry.writer` / `iam.serviceAccountUser`（対象 `amazon-profit-admin@…`）/ `storage.objectAdmin` / `serviceusage.serviceUsageConsumer`）を付与
3. 任意: GitHub の **Environments → `production`** に必須レビュアーを設定すると、本番反映前に人手の承認ゲートを掛けられる

### 使い方

Actions 画面から **Run workflow**（既定は `dry_run` オフ・`skip_iap` オン）。まずは `dry_run=true` で認証と設定確認、その後 `skip_iap=true` で再デプロイ。

> 注意（既知の潜在バグ）: `app/gcs_credentials.materialize_credentials_value` は SA JSON を**本文のまま env で渡すと**、長さがファイル名上限を超えて `OSError: File name too long` になる。CI では本文をファイルへ書いてパス渡しするため回避済み。`AIC_GCS_CREDENTIALS` を JSON 本文で使う運用がある場合は、この関数に `OSError` ガードを足す修正を別途推奨（本 PR の対象外）。
