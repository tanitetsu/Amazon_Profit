# Cloud Agent 開発環境（案 B）

正本。コード修正は **Cloud Agent**。自宅 PC のローカル Agent は **手動操作の確認**（管理画面・Chrome・シート見た目）と **デプロイ** 程度。

## 問題と抑え方

| 問題 | 抑え方 |
|---|---|
| Agent が全ユーザーシートを書き換えられる | ライブ Drive／Apps Script は **依頼があるときだけ**（従来ルール）。依頼なしの再ビルド・再配備・表示切替はしない |
| 認証 JSON がリポやチャットに漏れる | 中身はコミットしない・読まない・貼らない。Cursor Environment の **secrets** と GCS URI だけ |
| PC に未コミットの直しが残る | PC では原則コードを直さない。残ったら stash / commit してから Agent を出す |
| Cloud Agent に GCP SA は付かない | **`ADMIN_USE_ADC` は付けない**。Drive は運営 OAuth（GCS の `OPERATOR_TOKEN_GCS_URI`） |
| デプロイ脚本が Windows | 当面の Cloud Run デプロイは自宅 PC の `scripts\\deploy-admin.ps1` のまま |

反対意見: 認証をクラウドに置くと、誤操作の被害は PC 実行より大きい。その代わりスマホからも移行・取込確認まで完結できる。

## 役割分担

```text
【日常の修正】
  スマホ / PC の Cursor → Cloud Agent → 枝 → PR → main

【PC ローカル Agent】
  管理画面の手動クリック確認、Chrome での見た目、デプロイ
  同じファイルを Agent 作業中にローカルで直さない

【デプロイ】
  最新 main から1本。当面は PC の deploy-admin.ps1
```

## Cursor Environment に載せるもの

リポの `.cursor/environment.json` は **依存関係の install だけ**。秘密情報はここに書かない。

ダッシュボードの Environment **secrets / 環境変数**（値はチャットに貼らない）:

| 名前 | 用途 |
|---|---|
| `AIC_GCS_CREDENTIALS` | GCS 用 SA JSON のパス（名簿・`app_config`・Gmail トークン読み） |
| `GOOGLE_APPLICATION_CREDENTIALS` | 上と同じファイルでも可（`AIC_GCS_CREDENTIALS` 優先） |
| `APP_CONFIG_GCS_URI` | 運営設定 `gs://…/app_config.json`（`USERS_CONFIG_GCS_URI` でも可） |
| `OPERATOR_TOKEN_GCS_URI` | 運営 OAuth JSON（Drive / Sheets / gmail.send） |
| `OAUTH_CLIENT_GCS_URI` | OAuth クライアント JSON（トークン更新に使う） |

**付けない**

- `ADMIN_USE_ADC=1`（Agent にランタイム SA が無い。付けると Drive が ADC になり失敗する）
- `secrets/` 配下の実ファイルをリポへコピーすること

Cloud Run 本番はこれまでどおり `ADMIN_USE_ADC=1` + ランタイム SA。Agent と本番で認証の足は分ける。

## 運営が一度だけやること

1. Cursor でこのリポの Cloud Agent Environment を作る（未作成なら）
2. 上表の変数を Environment secrets に入れる（Cloud Run と同じ GCS URI。SA JSON は中身をリポに置かない）
3. 以降の Agent はその Environment から起動する
4. 自宅 PC の作業コピーはきれいにする（未コミットを残さない）

この文書を書いた時点の Agent 実行には Environment が未接続だった。変数を入れた **次の** Agent から Drive / Gmail に届く。

## 動作確認（Agent 側・値は出さない）

Environment 接続後、中身を print せず:

- `APP_CONFIG_GCS_URI` / `OPERATOR_TOKEN_GCS_URI` が `gs://` で始まっている
- `load_users_config()` が例外なく返る
- `load_operator_credentials()` が例外なく返る

失敗したら URI と SA の権限（該当バケット読取）を確認する。JSON 本文はログに出さない。
