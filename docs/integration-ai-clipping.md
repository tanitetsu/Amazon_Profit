# AI_Cripping × amazon-profit-viewer 統合方針（正本）

最終更新: 2026-08-05  
状態: **決定済（ロードマップに沿って実装）**。退会アーカイブ（`setting/quitted-user/`）仕様を反映。seed 元を `asamiodaka.b` / `setting/template/` に更新。

## 目的

出品〜利益管理を **同一ユーザー名簿・同一ロール語彙** で運用する。最終形は **モノリポ（案 C）**。

| プロダクト | 役割 |
|---|---|
| **AI_Cripping**（リポ: `AI_Cripping`） | メルカリ検索・オートクリップ・データ閲覧 |
| **amazon-profit-viewer**（本リポ） | Amazon 利益シート・管理者コンソール・Gmail 同意メール＋5 分ポーリング取込 |

## 非目的（当面）

- いきなりモノリポへ一括移行すること（段階的に進める）
- エンドユーザー向け単一 SPA の完成（認証・Gmail 連携は後続）
- IAP 許可リストの完全自動同期は未対応（ユーザー追加時に対象 Cloud Run へ resource-level 付与は実装済。全サービスの完全同期 UI は当面対象外）

## 決定事項

| 項目 | 決定 |
|------|------|
| 最終形 | **モノリポ**（案 C）。当面は案 A で名簿・プロビジョンを一本化してから寄せる |
| 名簿の正本 | **AI_Cripping 側**（GCS `setting/user-list.csv`。列: `ユーザーID`,`ロール`）。gmail は `{user_id}@gmail.com` |
| 名簿は全体で一つ | Admin 追加／削除は必ず正本を更新。本リポの `config/app_config.json` は運営 Drive 設定のみ（ユーザー配列なし） |
| 退会リスト | GCS `setting/quitted_user.txt`（1行1 ID。削除時に追加、再登録時に削除）。設定アーカイブは従来どおり `setting/quitted-user/{id}/` |
| `user_id` | メールの `@` より前（例: `asamiodaka@gmail.com` → `asamiodaka`） |
| ロール語彙 | `Admin` / `Exclusive` / `Normal`（AI_Cripping と同一） |
| Drive 共有 | ロールに関わらずファイルは **Editor** 共有 |
| シート範囲保護 | **Admin: 保護なし**（`apv:*` を付けない／既存は除去）。**Exclusive / Normal: 現行仕様**（自動列・ダッシュボード保護。青列と状態は編集可。buyer-cancel 行ロックあり） |
| Admin 追加時 | ①同年次シートが無ければテンプレ copy。**あれば再利用**②保護③ Editor 共有 ④正本 `user-list.csv` に ID・ロール ⑤ `quitted_user.txt` から除外 ⑥ GCS：`setting/quitted-user/{id}/` があれば復元、無ければ Book 4 seed ⑦ Gmail 同意メール ⑧ IAP |
| 削除時 | 全年度ブックの Drive 共有解除＋正本名簿から除外＋**`quitted_user.txt` に追記**＋ **Gmail トークン／seen 削除**＋IAP 解除。シート本体・`scraping-data/{id}/`・`log/{id}/` は残す。**`setting/user/{id}/` は `setting/quitted-user/{id}/` へ移動** |
| GCS seed（Book 4） | フォルダ `setting/user/{id}/`・`scraping-data/{id}/`・`log/{id}/`。**ng / replace / excluded / ids** = `asamiodaka.b` からコピー（全ロール）。**feed / price** = `setting/template/` からコピー（初回に `asamiodaka.b` 由来で作成。price はヘッダーのみ）。search/queue=空、fee=`10`。既存オブジェクトは再作成しない。Admin コンソール（本リポ）が名簿・seed の正本ライター |
| 退会アーカイブ | **`setting/quitted-user/`**（`setting/` 直下）。削除時に `setting/user/{id}/` を `setting/quitted-user/{id}/` へ移動。テンプレ Admin **`26964u` の削除・退避は禁止** |
| 再追加＋ quitted 復元 | 同一 ID が `setting/quitted-user/{id}/` にあれば **新規 seed せず** そのフォルダを `setting/user/{id}/` へ戻す。ロールは再作成ダイアログで選んだ値を名簿に書く（GCS 設定ファイルはそのまま） |
| 異常時（両パス存在） | **削除（archive）**: active を正として quitted を置換し、active→quitted へ移動。**再追加（restore）**: active を優先し quitted は触らず警告 |
| Admin UI（AI_Cripping） | AutoClipping／データビュアーで退会ユーザーを **別セクション**表示（「（退会）」）。**読取のみ**。退会選択中の **クリップ実行開始は禁止** |

### seed とは

**seed** = 新規ユーザー用に GCS へ初期ファイル一式を置くこと。  
例: `ng_word.txt` / `replace_word.txt` / `excluded_user.txt` / `ids_already_got.txt` を `asamiodaka.b` からコピー（全ロール）、`amazon_feed_template.json` とヘッダーのみ `price.csv` を `setting/template/` からコピー、`amazon-fee.txt`=`10`、空の search/queue。  
**quitted 復元時は seed しない**（退避フォルダを元の `setting/user/{id}/` に戻すだけ）。

### seed / quitted 方針（明文化）

1. 現行ユーザー設定の正パスは **`setting/user/{user_id}/`**（`setting/{user_id}/` は旧パス。移行スクリプトで移動）
2. 退会ユーザー設定は **`setting/quitted-user/{user_id}/`**（`scraping-data` / `log` は移動しない＝案 A）
3. 名簿は **`setting/user-list.csv`**。退会者は名簿から除外し **`setting/quitted_user.txt`** に追記（再登録時は txt から除外）
4. 新規 seed は上記表。ただし quitted フォルダに同一 ID があれば復元を優先
5. AI_Cripping 側 `multi_user_ops` は現行プレフィックスを読み、欠けていれば同方針で埋める（上書きしない）。UI で Admin が他ユーザーとして振る舞う機能は持たない（管理は amazon-profit-viewer）
6. legacy flat（`setting/ng_word.txt` 等）および共有 `setting/excluded_user.txt` は移行後のフォールバック／削除対象
7. 名簿に無いが `setting/user/{id}/` に残る過去データは **一括で `setting/quitted-user/{id}/` へ移動**（`26964u` は対象外）。退会リスト txt も同期する
8. 名簿・退会リストは編集時に空行を除去して書き戻す
9. 除外出品者はユーザー別。欠落時は `asamiodaka.b` と同内容で埋める

## ロードマップ

1. **第1段（進行中）**: 管理者コンソールを正本ライターにする。追加／削除で `user-list.csv`＋GCS seed＋利益シート。保護はロール分岐。ユーザー追加時に AI_Cripping Cloud Run（既定 `ai-cripping-data-viewer`）へ IAP resource-level 付与／削除時に解除
2. **第2段**: Admin UI からクリップ／シートを開くリンク統合
3. **第3段（最終）**: モノリポ＋単一 Cloud Run（案 C）。GCE ワーカー境界は維持

## 反対意見・リスク（意識する）

- 正本を二重運用のままにすると、片方だけ追加が再発する → 追加 API は必ず正本を先に／同時に更新
- モノリポを急ぐと Selenium／GCE と Drive 運用が同じ CI で壊れやすい → 名簿統合を先に完了させる
- Admin「保護なし」は誤編集リスクがある → Admin は運営アカウントに限定する運用

## 実装メモ

- GCS バケット既定: `public-data-for-amazon`（環境変数 `GCS_BUCKET` / `AIC_GCS_BUCKET`）
- 認証: `AIC_GCS_CREDENTIALS` または AI_Cripping の `secrets/gcs_service_account.json`（中身はコミットしない）
- seed は AI_Cripping `multi_user_ops.ensure_user_settings_seeded` と互換のオブジェクト配置
