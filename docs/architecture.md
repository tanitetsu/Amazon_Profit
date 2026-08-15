# Amazon 利益管理メール連携（設計正本）

運用: [`operations.md`](operations.md) · 統合: [`integration-ai-clipping.md`](integration-ai-clipping.md) · **本番前**: [`pre-production-checklist.md`](pre-production-checklist.md)

## 目的

紐づいたユーザーの Gmail から Amazon 出品関連メールを抽出し、運営 Drive 上の利益管理スプレッドシートへ自動反映する。

## 非目的

- ユーザーが追記先スプレッドシートを選ぶ・変更すること
- 購入者の支出記録（本テンプレートは出品者向け利益管理）
- パスワード／アプリパスワードの保管
- Excel / JSON からの一括投入（デモ再投入含む）

## アカウント役割

| 役割 | アカウント | 用途 |
|---|---|---|
| 運営 Drive | `26964u@gmail.com` | `User_Acounting` フォルダを所有し、ユーザー別シートを保存 |
| 連携ユーザー（初回） | `asamiodaka@gmail.com` | Gmail 読み取り対象。シート名は `@` より前（`asamiodaka`） |

## スプレッドシート仕様

**レイアウト・メール取込の決定事項正本:** [`docs/sheet-and-mail-spec.md`](sheet-and-mail-spec.md)（Book 3 確定版）  
コード正本（実装後）: `app/schema.py` / `sheet_builder.py` / `sheet_style.py`

### 方針（要約）

- ファイル名: `amazon-profit_{user_id}_{yyyy}.xlsx`（年次で新規作成）
- 保存先: `26964u@gmail.com` / `User_Acounting/`（ユーザーは変更不可）
- シート名 `ダッシュボード`。新規ブックは **テンプレ `amazon-profit_TEMPLATE.xlsx` を copy**
- 月次タブ `YYYY-MM` はテンプレ内 `月次テンプレート` の duplicate。ユーザー本は copy 直後に種シートを**非表示**（`hide_month_template_sheet`）。テンプレ本体の表示は運営編集可（再ビルド時は表示推奨）。明細は最終行の次へ APPEND（☑は APPEND／Excel データ行のみ。空スロットに☑なし）。テンプレ詳細は 2000 行分横結合済み（`build_workbook_template.py`）
- キャンセル☑→状態 ASCII `-` はテンプレ継承の Apps Script simple `onEdit`。新規は copy のみ（プロビジョンは API 再付与しない）。**Agent／自動化はライブテンプレと Apps Script を変更しない**。運営の手動変更（Save 含む）はあり得る—copy は当時のテンプレ内容を継承
- Excel 取込は値・☑・リンクのみ（月次の再スタイルなし）
- 欠月があれば間の月も作成。月別内訳・タブは降順。メタの **最終自動更新** を毎回更新
- 状態列 `○` / `×` / `-` / `返品` が取引ステータスの正。売上系集計は **状態=○ のみ**
- 明細の販売額はメールの「価格」「税金」を別列で持つ（税込へ合算しない）。Pt 欠落時のみ (販売価格+税金)×1%
- 編集可（薄青）: 仕入金 / 諸費用 / 発送日 / 仕入 / 発送 / キャンセル / 完了 / コメント
- 範囲保護: **Admin は保護なし**。Exclusive / Normal は自動列・ダッシュボードを保護（editors=運営＋SA）
- レイアウト正本はテンプレ（単位幅・列は `app/schema.py`）。`app/template_ops.py` が運用パス
- 既存本番は移行しない。詳細・検知メール・メルカリ規則は `sheet-and-mail-spec.md`
- ユーザー統合正本: [`docs/integration-ai-clipping.md`](integration-ai-clipping.md)（名簿正本は AI_Cripping `user-list.csv`。最終形モノリポ）
- **開発正は Cloud Agent**（認証の載せ方: [`cloud-agent-environment.md`](cloud-agent-environment.md)。`AIC_GCS_CREDENTIALS` はパスまたは JSON 本文）。PC ローカル Agent は手動確認とデプロイ程度

### Sheets API 書き込み

- 書き込みは `app/sheets_retry.py` 経由（`batchUpdate` / values）
- **429 / クォータ**時は指数バックオフ＋ジッターでリトライ（黙ってスタイルを捨てない）
- チャンク間に短い間隔を置き、レート超過を抑える
- `unmerge` / `addBanding` の無害な 400 のみソフトスキップ可。それ以外の失敗はジョブ失敗として送出

## 認証

- OAuth 同意画面のアプリ名: **`amazon-profit-viewer`**（Cloud Run サービス名も同様）
- アプリ利用者ログイン: Firebase Auth（Google）※後続（当面は管理者コンソール経由）
- **Gmail 読み取り（案 B・実装）**: ユーザー本人が同意リンクを開いて `gmail.readonly` を許可
  - **トリガー**: 管理者画面の「新規ユーザー追加」→ 運営 Gmail（`gmail.send`）から同意メール送信
  - リンク: `{PUBLIC_BASE_URL}/oauth/gmail/start?invite=…`（署名付き・7 日有効）
  - コールバック: `/oauth/gmail/callback` → トークン保存 → 初回取込
  - **継続**: 5 分ポーリング（取込 ＋ **access token 再取得／refresh_token 活性維持**）
  - トークン: `secrets/gmail_tokens/` または GCS `gmail_tokens/`（`USERS_CONFIG_GCS_URI` のバケット）
  - 取込済み message id: `secrets/gmail_seen/` または GCS `gmail_seen/`（cold start 耐性のため Cloud Run は GCS）
  - ポーリング実行記録: `secrets/mail_poll_runs/` または GCS `mail_poll_runs/`（日付フォルダ。Admin `/mail-poll-runs` で日付／ユーザー／エラーあり閲覧。日付空欄は保持期間内横断。約 30 日保持）
  - リダイレクト URI を OAuth クライアントに登録（例: `http://127.0.0.1:5055/oauth/gmail/callback` と公開 `PUBLIC_BASE_URL` の同パス）
  - ポーリングはトークン維持＋メール取込のみ。全ブック横断の cancel sync は載せない（ユーザー増で OOM／timeout）
- シート作成・書込: ローカルは運営 OAuth。Cloud Run はランタイム SA。**同意メール送信だけは運営ユーザー OAuth**（`OPERATOR_TOKEN_GCS_URI` 可）

ユーザーが用意するもの: 届いた同意メールのリンクから、Amazon 通知が届く Google アカウントで許可すること。

### 費用概算（5 分ポーリング ＋ トークン維持）

#### トークンの寿命（前提）

| 種類 | 寿命 | 本システムの扱い |
|---|---|---|
| **Access token** | 約 1 時間 | 5 分ポーリングのたびに期限前再取得して保存 |
| **Refresh token** | 短い固定期限はない。長期間未使用・パスワード変更・ユーザー取り消し等で無効化されうる | 同上ポーリングで必ず refresh ＋ Gmail `getProfile` を叩き、**未使用放置を防ぐ** |

ユーザーに再同意を求めるのは、refresh が無効化されたときだけ（削除／取り消し／長停止後など）。

#### 1 ユーザーあたり月額（Cloud Run ＋ Scheduler・概算）

前提: asia-northeast1、リクエスト課金、5 分間隔（8,640 回/月）、空振り多め。

| 内訳 | 1 ユーザー増分の目安 |
|---|---|
| Cloud Run CPU/メモリ（list + token refresh + 稀な書込） | **$0.25〜0.60 / 月** |
| Cloud Scheduler・Gmail/Sheets API | 実質 **$0**（クォータ内） |
| トークン再取得（oauth2 token + getProfile） | API 課金なし。Run 時間に **+$0.05 未満 / 月** 程度 |
| **合計（1 ユーザー）** | およそ **$0.3〜0.7 / 月** |

| 全体 | 目安 |
|---|---|
| ベース（Scheduler 空振り・サービス常駐分） | **$0.5〜1.5 / 月**（ユーザー数にほぼ非依存） |
| ローカル PC で `--loop` のみ | クラウド課金 **ほぼ $0 / ユーザー** |

※ 注文メールが多い月は Sheets 書き込みで少し上がる。コールドスタートが多いとベース側が増える。

### 管理者 UI（Gmail 関連）

- 新規追加: シート作成・共有のあと同意メール送信（未連携時）
- 再追加かつ連携済: その場で取込
- 一覧: Gmail 連携済バッジ / 未連携なら「同意メール再送」

### OAuth 同意画面（現状）

同意画面は **本番公開済み**・**Google 検証審査中**。テストユーザー運用は終了方針。Admin 追加 → 同意メール → 本人許可で足りる。審査完了まで未検証アプリ警告や一部制限があり得る。

## 管理者 UI

### ローカル

- 起動: `.\scripts\start-admin.ps1` → http://127.0.0.1:5055/（起動時に GitHub `main` との同期確認。詳細: [`git-workflow.md`](git-workflow.md)）
- 認証は未実装（`127.0.0.1` のみ。運営マシン想定）
- Drive/Sheets: 運営 OAuth（`secrets/operator_token.json`）。**gmail.send 追加後は `python scripts/oauth_operator.py` を再実行**
- ユーザー一覧正本: AI_Cripping GCS `setting/user-list.csv`（運営設定のみ `config/app_config.json`）
- ポーリング: `.\.venv\Scripts\python.exe scripts\poll_gmail_ingest.py --loop --interval-sec 300`

### Cloud Run（本番管理画面）

- サービス名: `amazon-profit-viewer`
- アクセス: **IAP**（Google ログイン）。運営例: `26964u@gmail.com`。一般ユーザーは Admin 追加時に `ai-cripping-data-viewer` へ自動付与（削除時解除）
- **管理者 UI の未認証公開は禁止**（`--no-allow-unauthenticated` + IAP）
- Drive/Sheets: ランタイム **サービスアカウント**（ADC）。`User_Acounting` を SA に Editor 共有
- ユーザー一覧正本: GCS（`USERS_CONFIG_GCS_URI`）
- **同意 callback / ポーリング URL**: 別サービス `amazon-profit-oauth`（**未認証公開**・`APP_SURFACE=public`）。Admin の IAP URL をメールに載せない。`PUBLIC_BASE_URL` はその公開オリジン
- 環境変数例: `PUBLIC_BASE_URL`, `MAIL_POLL_SECRET`, `OPERATOR_TOKEN_GCS_URI`, `OAUTH_CLIENT_GCS_URI`, `GMAIL_INVITE_SECRET`, **`ADMIN_USE_ADC=1`（必須）**
- デプロイ: `.\scripts\deploy-admin.ps1 -ProjectId <GCP_PROJECT>`（Admin + 公開 OAuth を同スクリプト。遅れ／分岐が残れば中止）

### 本番展開時の認証まわり（指摘・確認必須）

ローカルでは Drive/Sheets は **運営 OAuth**（`operator_token.json`）。Cloud Run では **`ADMIN_USE_ADC=1` + ランタイム SA**。

| 確認 | 内容 |
|---|---|
| SA 共有 | `User_Acounting` をランタイム SA に **Editor** 共有済みか（未共有だと取込が `folder … not reachable by service account` で落ちる） |
| テンプレ copy / プロビジョン書込 | **運営ユーザー OAuth**（`OPERATOR_TOKEN_GCS_URI`）。SA は My Drive 容量がなく、コピー直後の保護 editors からも落ちるため `storageQuotaExceeded` / 保護セル編集エラーになる |
| `ADMIN_USE_ADC` | 本番は `1`。ローカルは付けない（または `0`）。`GOOGLE_APPLICATION_CREDENTIALS` だけでは ADC にしない（AI_Cripping GCS 用と混線するため） |
| 同意メール送信 | SA では送れない → `OPERATOR_TOKEN_GCS_URI`（運営ユーザー OAuth・gmail.send）を本番にも載せる |
| OAuth 同意画面 | 本番公開済み・審査中（上記「OAuth 同意画面」）。テストユーザー運用は終了方針 |

### 機能（ローカル / Cloud 共通）

- ユーザー一覧と **シートを開く**（ワンクリック）
- **テンプレートを表示**: 正本テンプレートを別タブで直接開く
- **新規ユーザー追加**: テンプレ copy → 当月タブ作成 → ロール別保護（Admin=なし／他=現行）→ Editor 共有（Drive 通知でシート URL）→ AI_Cripping `user-list.csv`＋GCS seed → users config → **Gmail 同意メール**（本文にもシート URL）。ユーザー作業は同意リンク＋シートを開くのみ（Apps Script 保存なし）
- **既存ユーザーの再追加**: 同年次ファイルがあれば **必ず再利用**（レイアウト再構築しない）。保護をロールに合わせて刷新し、共有・名簿・config を更新。空に戻すのは `rebuild=True` のみ
- **ユーザー削除**: 共有解除＋正本名簿から除外＋config 除外＋ Gmail トークン削除（シート本体・`scraping-data`/`log` は残す）。**`setting/user/{id}/` → `setting/quitted-user/{id}/` へ移動**（詳細は integration 正本）
- **空レイアウトへの強制 rebuild** はコード上 `provision_user(..., rebuild=True)` のみ（Admin UI からは出さない）

詳細: [`docs/integration-ai-clipping.md`](integration-ai-clipping.md)

## 本番の意味（現状）

| 層 | 状態 | 備考 |
|---|---|---|
| 利益管理スプレッドシート（Drive） | **本番データ** | `26964u@gmail.com` / `User_Acounting` |
| 管理者 UI（ローカル） | 開発・緊急用 | `127.0.0.1:5055`。公開しない |
| 管理者 UI（Cloud Run） | IAP + SA | 未認証公開しない |
| Gmail 同意・ポーリング HTTP | 公開ベース必須 | `PUBLIC_BASE_URL`。ローカルは同一プロセス可 |
| エンドユーザー向け閲覧 SPA | 後続 | Firebase Auth 等 |

### 既存シートを空に戻すとき（例外）

`initialize_workbook` は Overview＋**当月のみ**を残し、他の月次タブを削除して中身をクリアする。本番で誤実行すると注文データが消える。Admin の「追加」では走らない。
