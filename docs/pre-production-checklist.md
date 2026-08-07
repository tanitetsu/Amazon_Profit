# 本番前チェックリスト（amazon-profit-viewer × AI_Cripping）

単一プロダクトとして本番に載せる前の確認正本。詳細設計は各 docs を参照。

## A. OAuth / Gmail 連携（amazon-profit-viewer）

- [x] OAuth 同意画面アプリ名: `amazon-profit-viewer`
- [x] **本番公開**（In production）済み・**Google 検証審査中**。テストユーザー運用は終了方針。審査完了まで未検証アプリ警告や一部制限があり得る
- [ ] Gmail API 有効
- [ ] ユーザー同意用クライアントにリダイレクト URI 登録  
      `{PUBLIC_BASE_URL}/oauth/gmail/callback`（ローカル検証なら `http://127.0.0.1:5055/...` も可）
- [x] 運営 OAuth 再認可済み（Drive / Sheets / script / **gmail.send**）→ GCS `secrets/operator_token.json` ＋ Cloud Run `OPERATOR_TOKEN_GCS_URI`
- [x] `PUBLIC_BASE_URL` = 公開サービス `amazon-profit-oauth` のオリジン（**IAP 配下の Admin URL をメールに載せない**）
- [x] OAuth **Web** クライアントに `{PUBLIC_BASE_URL}/oauth/gmail/callback` を登録し、JSON を `secrets/oauth_client.json`（→ GCS）へ
- [x] `MAIL_POLL_SECRET` / `GMAIL_INVITE_SECRET` 設定
- [x] Cloud Scheduler `amazon-profit-mail-poll` が **5 分**で `POST` 公開 OAuth の `/api/internal/mail-poll`（`attemptDeadline` ≥ OAuth timeout）
- [x] 公開 OAuth `amazon-profit-oauth`: メモリ **2Gi** / timeout **900s**（mail-poll OOM 対策。`deploy-admin.ps1` と一致）
- [x] 取込済み Gmail id は同バケット `gmail_seen/`（トークン同様 GCS。cold start 再取込防止）
- [x] ポーリング実行記録は同バケット `mail_poll_runs/`（Admin `/mail-poll-runs`。約 30 日）

## B. Cloud Run / IAP / SA（利益シート Admin）

- [x] サービス `amazon-profit-viewer` は **未認証公開禁止**（IAP + invoker）
- [x] `ADMIN_USE_ADC=1` + ランタイム SA
- [ ] Drive フォルダ `User_Acounting` をランタイム SA に **Editor** 共有
- [x] `USERS_CONFIG_GCS_URI` / `APP_CONFIG_GCS_URI` = 運営設定 `app_config.json`（ユーザー名簿は含まない）
- [x] Gmail トークンは同バケット配下 `gmail_tokens/`
- [x] Gmail seen（取込済み id）は同バケット `gmail_seen/`
- [x] ポーリング実行記録は同バケット `mail_poll_runs/`
- [x] 名簿正本 `setting/user-list.csv`／退会リスト `setting/quitted_user.txt`
- [x] 公開面 `amazon-profit-oauth`（同意 + mail-poll）。メルカリ仕入金は `items/get` + DPoP
- [x] mail-poll に全ブック cancel sync を載せない（必要なら別ジョブ・低頻度）

## C. AI_Cripping / 共有 GCS

- [x] バケット（既定 `public-data-for-amazon`）へ両方から到達可能
- [x] ユーザー設定パスが **`setting/user/{user_id}/`**（旧 `setting/{user_id}/` は移行済み）
- [x] 退会アーカイブ **`setting/quitted-user/{user_id}/`**（マーカー作成済み。名簿外の残骸は一括移動済み）
- [x] 名簿 `setting/user-list.csv`（列: `ユーザーID`,`ロール`）
- [ ] legacy flat（`setting/ng_word.txt` 等）削除済み（移行後）
- [x] Admin 追加＝名簿 upsert ＋（quitted あれば復元／無ければ Book 4 seed。既存は再作成しない）＋ **IAP 自動付与**（`ai-cripping-data-viewer`）
- [x] ランタイム SA に `roles/iap.admin`（IAP roster sync）
- [x] AI_Cripping UI の利益シートリンクが現行 Cloud Run URL

## D. データ／運用ポリシー

- [x] ユーザー削除: Drive 共有解除・名簿除外・Gmail トークン削除。シート・scraping/log は残す。**setting/user → setting/quitted-user へ移動**（`26964u` 禁止）
- [x] Admin の退会ユーザー参照: AutoClipping／データビュアーで別セクション・**読取のみ**・クリップ実行禁止
- [x] ユーザー再追加は同年次ブックを**再利用**（上書き確認なし）。空レイアウトは `rebuild=True` のみ
- [ ] Excel 一括投入は非目的（必要なら `scripts/` の移行用のみ）
- [ ] secrets / debug ログをリポジトリに含めない

## E. 費用目安（参考）

5 分ポーリング＋トークン維持: **約 $0.3〜0.7 / ユーザー / 月** ＋ ベース約 $0.5〜1.5（Cloud Run）。詳細は `architecture.md`。

## 関連正本

| 内容 | 文書 |
|------|------|
| 利益シート設計 | `amazon-profit-mail/docs/architecture.md` |
| シート・メール仕様 | `amazon-profit-mail/docs/sheet-and-mail-spec.md` |
| 統合・名簿・seed | `amazon-profit-mail/docs/integration-ai-clipping.md` |
| 利益 Admin 運用 | `amazon-profit-mail/docs/operations.md` |
| AI_Cripping マルチユーザー | `AI_Cripping/docs/gcp-multi-user.md` |
