# 運用メモ（amazon-profit-viewer）

本番前チェック正本: [`pre-production-checklist.md`](pre-production-checklist.md)

## 起動・確認（ローカル）

```powershell
.\scripts\start-admin.ps1
```

起動時に GitHub の `main` とのズレを確認する（詳細: [`git-workflow.md`](git-workflow.md)）。Agent から起動するときは `START_ADMIN_NO_PAUSE=1`。

- URL: http://127.0.0.1:5055/
- 一覧 API: `GET /api/users` → `ok: true` とユーザー／年次ブック（`url` は Drive 上に実在するときのみ）
- 管理画面: ユーザー・年のプルダウンで絞り込み。存在しないファイルへの「開く」リンクは出さない
- Drive: `secrets/operator_token.json`（`python scripts/oauth_operator.py`）
  - **gmail.send を scopes に追加したあと必ず再認可**（同意メール送信用）
  - 本番の新規ユーザー追加・同意メールが `invalid_grant` / `token expired or revoked` のときは、GCS の運営トークンが失効している。PC で `python scripts/oauth_operator.py` を `26964u@gmail.com` として再実行し、`secrets/operator_token.json` を `OPERATOR_TOKEN_GCS_URI` へ上げる（デプロイ脚本がローカルファイルを見つければコピーする）。Agent からはブラウザ同意できない
- Gmail ポーリング（5 分）:

```powershell
.\.venv\Scripts\python.exe scripts\poll_gmail_ingest.py --loop --interval-sec 300
```

または Admin 起動中に `GET/POST http://127.0.0.1:5055/api/internal/mail-poll`（localhost は secret なし可。本番は `MAIL_POLL_SECRET`）

### Gmail 連携（案 B）セットアップ

1. GCP で **Gmail API** を有効化
2. OAuth **Web** クライアントにリダイレクト URI を登録（Desktop クライアントでは不可）  
   - ローカル: `http://127.0.0.1:5055/oauth/gmail/callback`  
   - 公開: `{PUBLIC_BASE_URL}/oauth/gmail/callback`（`amazon-profit-oauth`）
   - JSON を `secrets/oauth_client.json` に置き、デプロイで GCS へ同期
3. `python scripts/oauth_operator.py`（運営アカウント・gmail.send 含む）
4. 同意メール内リンク用に `PUBLIC_BASE_URL`＝公開 OAuth サービス（デプロイ脚本が設定）
5. Admin でユーザー追加 → 対象 Gmail に同意メール → ユーザーが許可 → 初回取込＋以降ポーリング

**OAuth 同意画面:** 本番公開済み・**審査中**（正本: `docs/architecture.md`）。テストユーザー運用は終了方針。審査完了まで未検証アプリ警告や一部制限があり得る。

**本番展開チェック（Drive / 取込）:** Cloud Run では `ADMIN_USE_ADC=1` とランタイム SA でシート書込する。`User_Acounting` をその SA に Editor 共有しないと、Gmail 同意後の取込が `folder … not reachable by service account` で失敗する。同意メール送信用に運営 OAuth（`OPERATOR_TOKEN_GCS_URI`）も別途必要。詳細は `docs/architecture.md` 「本番展開時の認証まわり」。

### 環境変数（Gmail）

| 変数 | 意味 |
|---|---|
| `PUBLIC_BASE_URL` | 同意リンクのオリジン |
| `MAIL_POLL_SECRET` | `/api/internal/mail-poll` の共有秘密 |
| `OPERATOR_TOKEN_GCS_URI` | Cloud Run で運営 OAuth JSON（gmail.send） |
| `GMAIL_INVITE_SECRET` | 同意 invite 署名鍵（未設定時は自動生成ファイル） |
| `GMAIL_TOKENS_GCS_PREFIX` | ユーザー Gmail トークンの GCS 先（未設定時は app_config バケット配下 `gmail_tokens/`） |
| `GMAIL_SEEN_GCS_PREFIX` | 取込済み message id の GCS 先（未設定時は同バケット `gmail_seen/`）。**Cloud Run では必須相当**（ローカルディスクだけだと cold start で再取込が走る） |
| `MAIL_POLL_RUNS_GCS_PREFIX` | ポーリング実行記録の GCS 先（未設定時は同バケット `mail_poll_runs/`）。Admin の「ポーリング実行記録」画面が参照 |
| `MAIL_POLL_MAX_WORKERS` | 1 リクエスト内のユーザー並列数（既定 `1`。httplib2 非スレッドセーフ回避） |
| `MAIL_INGEST_MAX_PER_POLL` | 1 ユーザーあたり未処理メールの取得上限（既定 `25`。5 分 tick で追い付き） |
| `MAIL_INGEST_BUDGET_SEC` | 1 ユーザー ingest のソフト上限秒（既定 `480`） |

本番ポーリング: Cloud Scheduler ジョブ `amazon-profit-mail-poll`（`*/5` Asia/Tokyo）→ 公開 OAuth の `POST /api/internal/mail-poll?max_results=20`（`X-Mail-Poll-Secret`）。初回 catch-up は数 tick に分割（seen は GCS に逐次保存）。

- OAuth サービス: **メモリ 2Gi** / **timeout 900s** / concurrency 1（`deploy-admin.ps1`）。Scheduler `attemptDeadline` も **900s** 以上に合わせる
- クエリ: `?gmail=` で1ユーザーのみ（将来のユーザー単位ジョブ用）。`?workers=` で並列数上書き
- 実行記録: 毎回 `mail_poll_runs/{yyyy-mm-dd}/` へ保存（ローカルは `secrets/mail_poll_runs/`）。Admin `/mail-poll-runs` で日付（空欄＝指定なし・保持期間内）・ユーザー・「エラーあり」絞り込み。保持約 30 日
- **cancel☑→状態の全ブック同期を mail-poll に載せない**（OOM／timeout の主因）。必要なら別ジョブ・低頻度・欠落ブックはスキップ

### 費用（5 分・1 ユーザー）

詳細は [`architecture.md`](architecture.md)。要約: Cloud Run 利用時 **およそ $0.3〜0.7 / ユーザー / 月**（トークン再取得込み）＋ベース $0.5〜1.5。ローカルポーリングならほぼ $0。

## スモーク（ローカル）

```powershell
.\.venv\Scripts\python.exe -c "from app.schema import SUMMARY_SHEET; from app.sheet_builder import build_summary_grid, month_sheet_skeleton, period_from_months; m=['2026-08']; ps,pe=period_from_months(m); assert 'ダッシュボード' in build_summary_grid('a@b.com',2026,ps,pe,m)[0][0]; assert month_sheet_skeleton(m[0])[0][0]==m[0]; print('ok')"
```

## Cloud Agent 開発（案 B）

日常のコード修正は Cloud Agent。PC ローカル Agent は手動確認程度。  
本番デプロイは依頼があれば `./scripts/deploy-admin.sh`。認証の載せ方: [`cloud-agent-environment.md`](cloud-agent-environment.md)

## 販売価格 / 税金レイアウト移行

既存の月次・ダッシュボード・テンプレを「税込価格」1列から「販売価格＋税金」へ直す:

```powershell
.\.venv\Scripts\python.exe scripts\migrate_price_tax_columns.py --dry-run
.\.venv\Scripts\python.exe scripts\migrate_price_tax_columns.py
```

`asamiodaka.b` の年次ブックは変更前に同フォルダへ `.bak-price-tax-*` コピーする。既存行の金額はメールの「価格」「税金」を再入力し、ユーザー編集列は書かない。

## テンプレブック

**Agent／自動化:** ライブ Drive テンプレと bound Apps Script は変更しない（依頼なしの再ビルド・API 再配備・表示切替なし）。運営の手動編集（表示・スクリプト Save 含む）はあり得る。新規ユーザーは当時のテンプレ内容を copy 継承。ユーザー本は copy 直後に `月次テンプレート` を非表示。

- 正本: `User_Acounting/amazon-profit_TEMPLATE.xlsx`（`ダッシュボード`＋`月次テンプレート`。再ビルド時は種シート表示推奨）
- ID: `config/app_config.json` → `template_spreadsheet_id`
- 管理画面の新規ユーザーはテンプレ copy（`app/template_ops.provision_from_template` → `hide_month_template_sheet`）

## キャンセル☑ → 状態

テンプレ bound `onEdit` は有効化済み。新規ユーザーは copy で継承（ユーザー側のスクリプト保存不要。プロビジョンで API 再配備しない）。運営がテンプレ上で手動変更した場合は以降の copy がその内容を継承。

## 実データ投入（レガシー Excel → 年次ブック）

```powershell
.\.venv\Scripts\python.exe scripts\bootstrap_2026_from_excel.py --excel "e:\DownLoad\Amazon利益管理シート①.xlsx"
```

- 対象: `amazon-profit_{user}_{year}.xlsx`
- テンプレ copy のあと **値・☑・リンクのみ**書き込み（月次のスタイル再適用はしない。結合・CF はテンプレ継承）
- 仕入金: Excel 値優先、空かつ `m_m` SKU はメルカリ API で補完
- 複数点プレースホルダ SKU（`N点の商品が販売されました`）は空に正規化

## 本番データ（Drive）

- フォルダ: `User_Acounting`（`26964u@gmail.com`）
- 初回ユーザー例: `amazon-profit_asamiodaka_2026.xlsx`
- レイアウト正本: 月次=`2026-04` ライブ、Overview=現行ライブ Overview → `app/schema.py` ほか
- **再追加で wipe しない**（`app/provision.py`）。空に戻すのは `rebuild=True` のみ
- 既存本番シートへ新レイアウトを載せる: `python scripts/migrate_cancel_layout.py`（列挿入・CF・取り消し線行の☑固定。月次データは消さない）

## Cloud Run デプロイ（IAP + SA）

未認証公開はしない。IAP で Google ログイン必須。

### 初回セットアップ

1. GCP プロジェクトを用意し、課金を有効化
2. デプロイ（API・SA・AR・GCS・Cloud Run・IAP をスクリプトが処理）:

Cloud Agent（依頼があるとき・最新 `origin/main`）:

```bash
./scripts/deploy-admin.sh --dry-run
./scripts/deploy-admin.sh --skip-iap
```

PC:

```powershell
.\scripts\deploy-admin.ps1 -ProjectId "positive-design-480606-c7"
```

デプロイ前に GitHub の `main` より遅れていないことを確認する（遅れ／分岐／衝突見込みが残れば中止。明示時のみ `--skip-git-sync-check` / `-SkipGitSyncCheck`）。Cloud Agent の脚本は GCS に既にある invite / mail-poll 秘密を回さない。

既定:

| 項目 | 値 |
|---|---|
| GCP プロジェクト | `positive-design-480606-c7` |
| サービス | `amazon-profit-viewer` |
| リージョン | `asia-northeast1` |
| URL | https://amazon-profit-viewer-mjqzkyqita-an.a.run.app |
| ランタイム SA | `amazon-profit-admin@positive-design-480606-c7.iam.gserviceaccount.com` |
| app_config.json | `gs://positive-design-480606-c7-amazon-profit-admin/config/app_config.json`（**正本は GCS**。リポには `config/app_config.example.json` のみ。ローカル `config/app_config.json` は gitignore。デプロイはローカルがあれば同期、無ければ既存 GCS を維持） |
| IAP 許可 | 運営 `26964u@gmail.com`（デプロイ時）。一般ユーザーは Admin 追加時に `ai-cripping-data-viewer` へ自動付与（`app/iap_access.py`） |

3. **必須（手動）**: `26964u@gmail.com` の Drive でフォルダ `User_Acounting` をランタイム SA に **編集者**共有
4. 表示された Cloud Run URL を Chrome で開き、`26964u@gmail.com` でログイン
5. ユーザー一覧が載ることを確認

再デプロイ（イメージのみ）:

```bash
./scripts/deploy-admin.sh --skip-iap
```

```powershell
.\scripts\deploy-admin.ps1 -ProjectId "<GCP_PROJECT_ID>"
```

IAP 手順をスキップする場合: `--skip-iap` / `-SkipIap`。ビルド省略: `--skip-build` / `-SkipBuild`。同期チェック省略（非推奨）: `--skip-git-sync-check` / `-SkipGitSyncCheck`。

### Cloud Agent デプロイ用 IAM（一度だけ）

デプロイに使う SA（`GCP_DEPLOY_CREDENTIALS` があればそれ、なければ `AIC_GCS_CREDENTIALS`）に、ランタイム SA `amazon-profit-admin@…` への `roles/iam.serviceAccountUser` と、プロジェクトの `roles/run.admin` / `roles/cloudbuild.builds.editor` / `roles/artifactregistry.writer` が必要。未付与なら PC の GCP オーナーで付与する（JSON はチャットに貼らない）。詳細: [`cloud-agent-environment.md`](cloud-agent-environment.md)。

### 環境変数（Cloud Run）

| 変数 | 意味 |
|---|---|
| `ADMIN_USE_ADC=1` | Drive/Sheets をランタイム SA（ADC）で呼ぶ |
| `USERS_CONFIG_GCS_URI` / `APP_CONFIG_GCS_URI` | 運営設定 `app_config.json` の GCS URI |
| `K_SERVICE` / `PORT` | Cloud Run が設定。gunicorn が `0.0.0.0:$PORT` で待受 |

### デプロイ方針

- **未認証** Cloud Run（`--allow-unauthenticated`）に管理者コンソールを載せない
- IAP 付き Cloud Run が本番管理画面。ローカルは開発・緊急用
- クラウド運用開始後、`app_config.json` の正本は GCS（ローカルと二重管理しない。リポには example のみ）。ユーザー名簿は `setting/user-list.csv`

## 禁止・注意

- `secrets/` の中身をチャットやコミットに出さない
- 既存の複数月タブがあるシートへ `initialize_workbook` を掛けない（当月以外が消える）
- 個人 Gmail Drive では SA が新規作成したファイルのオーナーは SA（フォルダは運営所有のまま）
