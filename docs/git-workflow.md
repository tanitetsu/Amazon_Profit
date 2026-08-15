# Git の使い方（amazon-profit-mail）

OAuth / 本番サービス名: **amazon-profit-viewer**。正本は常に **GitHub の `main`**。

**別リポ方針**（AI_Cripping と同一リポにしない）: [`docs/repo-boundaries.md`](repo-boundaries.md)

## 用語（やさしい説明）

| 言葉 | 意味 |
|------|------|
| **リポジトリ（リポ）** | このシステムのプログラム一式を置いている「保管庫」。GitHub 上と、自宅 PC のフォルダの両方にある |
| **Git** | 変更履歴を記録する仕組み。「いつ・何を直したか」を残せる |
| **GitHub** | その履歴をインターネット上に置く場所。スマホと PC の共通の正本になる |
| **main** | 「完成版・本番の元」になる本線。ここがいちばん大事 |
| **ブランチ（枝）** | main からコピーした作業用の脇道。試し直しもここに書く。失敗しても本線は傷つかない |
| **コミット** | 「この時点の変更を履歴に残す」操作。セーブポイントのようなもの |
| **push（プッシュ）** | 自分の PC／Agent にある履歴を、GitHub に送る |
| **pull（プル）** | GitHub の新しい履歴を、自分の PC に取り込む |
| **PR（プルリクエスト）** | 「この枝の修正を main に入れてよいか」を出す申請。確認してから本線に載せる |
| **merge（マージ）** | PR を承認して、枝の内容を main に取り込むこと |
| **デプロイ** | 直したプログラムを、実際の本番サイト（Cloud Run）に反映すること。Git に載せるのとは別作業 |
| **Cloud Agent** | スマホなどから Cursor 経由で動かす、クラウド上の AI 作業員。枝を切って直して PR までできる |
| **競合** | 同じファイルを別々に直した結果、どちらを残すか自動では決まらない状態 |
| **正本** | 「どれが正しい最新か」の基準。この運用では **GitHub の main** が正本 |

たとえ: **main＝本番に出す台本**、**ブランチ＝下書き**、**PR＝編集者への提出**、**デプロイ＝舞台に載せる**。

## スマホと自宅 PC（推奨のやり方）

両方から修正・本番反映できるようにする。正本は常に **GitHub の main**。  
**日常のコード修正は Cloud Agent**（詳細: [`cloud-agent-environment.md`](cloud-agent-environment.md)）。PC ローカル Agent は手動確認程度。デプロイは依頼があれば Cloud Agent の `scripts/deploy-admin.sh`。

```text
【修正】
  スマホ / PC → Cloud Agent が枝を作って直す → PR →（確認）→ main に merge
  自宅 PC のフォルダでは原則コードを直さない（未コミットを残すと Agent の枝切り替えが失敗する）

【デプロイ】
  必ず「いまの main の最新」から、同時に1本だけ
  Cloud Agent: ./scripts/deploy-admin.sh（ユーザーが明示したとき）
  PC: scripts\deploy-admin.ps1
```

### 守ること（競合を避ける）

1. **main にいきなり長く直さない**（短い緊急修正以外は枝＋PR）
2. **作業を始める前に最新を取る**（両端末で同期チェック）
3. **片方に直しっぱなしで放置したまま、もう片方で別修正しない**  
   → 先にコミットして push するか、いったん取り消す／退避する
4. **デプロイは1人・1本・最新 main だけ**（古い版を後から載せると、新しい修正が消える）
5. 別々の機能なら **別の枝** で進めてよい（ぶつかるのは同じファイルを直したときだけ）

### 両端末の警告（GitHub の main とのズレ・衝突）

スマホで直して GitHub に載ったあと、PC のフォルダは自動では新しくなりません（逆も同じ）。**pull / push 忘れ**と**ぶつかりそうな取り込み**を防ぐため、PC とスマホで同じ判定を使います。

| いつ | 何が起きるか |
|------|----------------|
| **PC** `scripts\start-admin.ps1`（手動） | 遅れていれば **「いま pull する？」**。**ぶつかりそうなら先に警告**し、既定では pull しない／強い確認。断っても **Enter 待ち** |
| **PC** `start-admin`（Agent・`START_ADMIN_NO_PAUSE=1`） | 警告のみ（入力待ちしない）。衝突見込みも表示 |
| **PC** `scripts\deploy-admin.ps1` | 先に pull を尋ね、遅れ／分岐／**衝突見込み**が残れば**デプロイ中止** |
| **Cloud Agent** `scripts/deploy-admin.sh` | `origin/main` と一致＋作業ツリーきれい。遅れ／分岐なら中止。`--dry-run` で認証だけ確認可 |
| **PC** 単体 | `.\check-git-sync.ps1 -PromptPull` |
| **スマホ / Cloud Agent** | 修正前に `./check-git-sync.sh --agent`（必須）。安全なら自動 pull。衝突見込みや分岐が残れば**止めて修正に入らない** |
| Cursor（両端末） | 上記を Rules で必須化 |

**ぶつかりそう、の意味:** 自分の修正（未コミット含む）と、取り込む `main` 側の変更が同じファイルに重なる／merge すると衝突する見込みがある状態。

**push 忘れ（端末の方が新しい）:** pull は**しない**。push を促すだけ。

遅れているときの対処（自分でやる場合）:

```powershell
# PC
git pull origin main
```

```bash
# スマホ / Cloud Agent
git pull origin main
# または
./check-git-sync.sh --agent
```

※ 未コミットの変更があるときや、main と分岐しているときは自動 pull しません（先に整理が必要）。

確認だけスキップしたいとき（非推奨）: 環境変数 `SKIP_GIT_SYNC_CHECK=1`、またはデプロイ時 `-SkipGitSyncCheck`。

### デバイス別の役割

| どこ | 修正 | デプロイ |
|------|------|------------------|
| スマホ（Cloud Agent） | 枝 → PR → merge まで。開始前に `./check-git-sync.sh --agent` | 依頼があれば `./scripts/deploy-admin.sh`（最新 `origin/main` のみ） |
| 自宅 PC | 枝 → PR、または短い修正後すぐ push。開始前に同期チェック | `scripts\deploy-admin.ps1` でも可 |

`main` に入ったら自動デプロイはしない（依頼があるときだけ）。

## 事前準備（1回だけ）

1. **Git for Windows**（導入済みならスキップ）
2. **名前とメール**（コミットに付く。Agent は設定しない）

```powershell
git config --global user.name "あなたの名前"
git config --global user.email "your@email.com"
```

3. ターミナルで Git が使えること（新しい PowerShell を開く）

```powershell
git --version
```

## 絶対にコミットしないもの

`.gitignore` で除外済み:

- `secrets/`（OAuth トークン・クライアント JSON）
- `.venv/`・`venv/`・`.env`
- `config/app_config.json`（正本は GCS。リポには example のみ）
- `.chrome-admin-profile/`・`*.log`

## 日常の流れ

```text
最新の main を取る → 枝を作る（または Agent に任せる）
  → 直す → 確認（テスト） → コミット → push → PR → merge
  → 必要ならデプロイ（最新 main・1本だけ）
```

### よく使うコマンド

```powershell
cd "E:\Web Projects\amazon-profit-mail"

# GitHub の最新を取り込む
git pull origin main

# 変更一覧
git status

# 差分
git diff

# テスト（コミット前推奨）
.\.venv\Scripts\python.exe -m pytest tests -q

# 記録（Agent に「コミットして」と頼んでも可）
git add -A
git commit -m "短い説明（なぜ直したか）"

# GitHub に送る
git push

# 履歴
git log --oneline -10
```

デプロイは git とは別です（`scripts\deploy-admin.ps1` など）。手順は [operations.md](operations.md)。

## GitHub 連携

リポジトリ名（GitHub）:

| ローカルフォルダ | GitHub リポ |
|------------------|------------|
| `AI_Cripping` | https://github.com/tanitetsu/AI_Clipping_Mercari_to_Amazon |
| `amazon-profit-mail` | https://github.com/tanitetsu/Amazon_Profit |

```powershell
cd "E:\Web Projects\amazon-profit-mail"
git remote -v
# origin  https://github.com/tanitetsu/Amazon_Profit.git
```

（`remote` が無い場合は `git remote add origin https://github.com/tanitetsu/Amazon_Profit.git`）

## AI_Cripping 側

フォルダ: `E:\Web Projects\AI_Cripping`（**別リポジトリ**）。  
同仕様の同期ガード: `check-git-sync.ps1` / `.sh`、`docs/git-workflow.md`。secrets はコミットしない。  
境界の正本: [`docs/repo-boundaries.md`](repo-boundaries.md)
