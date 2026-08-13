#!/usr/bin/env bash
# GitHub の main とローカルのズレを確認する（PC の check-git-sync.ps1 と同仕様）。
# 使い方:
#   ./check-git-sync.sh
#   ./check-git-sync.sh --fail-if-behind     # 遅れ／分岐／衝突見込みなら exit 1
#   ./check-git-sync.sh --prompt-pull        # 対話で pull するか尋ねる（端末）
#   ./check-git-sync.sh --pause-if-behind    # 残リスク時に Enter 待ち
#   ./check-git-sync.sh --agent              # スマホ/Cloud Agent 用（入力なし）
#       遅れのみ・衝突なし・きれい → 自動 pull
#       衝突見込み／未コミット＋遅れ／分岐 → 警告して exit 1（修正に入らない）
#       進みのみ（push 忘れ）→ pull せず警告（exit 0）
#   ./check-git-sync.sh --skip-fetch
# 環境変数 SKIP_GIT_SYNC_CHECK=1 なら何もせず成功終了

set -u

REMOTE="${REMOTE:-origin}"
MAIN_BRANCH="${MAIN_BRANCH:-main}"
FAIL_IF_BEHIND=0
PROMPT_PULL=0
PAUSE_IF_BEHIND=0
SKIP_FETCH=0
AGENT_MODE=0

for arg in "$@"; do
  case "$arg" in
    --fail-if-behind) FAIL_IF_BEHIND=1 ;;
    --prompt-pull) PROMPT_PULL=1 ;;
    --pause-if-behind) PAUSE_IF_BEHIND=1 ;;
    --skip-fetch) SKIP_FETCH=1 ;;
    --agent) AGENT_MODE=1; FAIL_IF_BEHIND=1 ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
  esac
done

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1

ok()   { printf '[git同期] %s\n' "$*"; }
warn() { printf '[git同期] %s\n' "$*" >&2; }
bad()  { printf '[git同期] %s\n' "$*" >&2; }

if [[ "${SKIP_GIT_SYNC_CHECK:-}" == "1" ]]; then
  warn "SKIP_GIT_SYNC_CHECK=1 のため確認をスキップしました。"
  exit 0
fi

if ! command -v git >/dev/null 2>&1; then
  warn "git が見つかりません。同期確認をスキップします。"
  [[ "$FAIL_IF_BEHIND" -eq 1 ]] && exit 1
  exit 0
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  warn "Git リポジトリではありません。同期確認をスキップします。"
  [[ "$FAIL_IF_BEHIND" -eq 1 ]] && exit 1
  exit 0
fi

REMOTE_REF="${REMOTE}/${MAIN_BRANCH}"

if [[ "$SKIP_FETCH" -eq 0 ]]; then
  ok "GitHub の ${MAIN_BRANCH} を確認しています..."
  if ! git fetch "$REMOTE" "$MAIN_BRANCH" >/dev/null 2>&1; then
    warn "fetch に失敗しました（ネット未接続など）。手元の ${REMOTE_REF} で比較します。"
  fi
fi

if ! git rev-parse --verify "$REMOTE_REF" >/dev/null 2>&1; then
  warn "${REMOTE_REF} がありません。remote 設定を確認してください。"
  [[ "$FAIL_IF_BEHIND" -eq 1 ]] && exit 1
  exit 0
fi

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '(不明)')"

counts() {
  behind="$(git rev-list --count "HEAD..${REMOTE_REF}" 2>/dev/null || echo 0)"
  ahead="$(git rev-list --count "${REMOTE_REF}..HEAD" 2>/dev/null || echo 0)"
  behind="${behind:-0}"
  ahead="${ahead:-0}"
}

is_dirty() {
  [[ -n "$(git status --porcelain 2>/dev/null || true)" ]]
}

dirty_paths() {
  git status --porcelain 2>/dev/null | while IFS= read -r line; do
    [[ -z "$line" || ${#line} -lt 4 ]] && continue
    path="${line:3}"
    path="${path## }"
    if [[ "$path" == *" -> "* ]]; then
      path="${path##* -> }"
    fi
    printf '%s\n' "${path//\\//}"
  done | sort -u
}

incoming_paths() {
  local base
  base="$(git merge-base HEAD "$REMOTE_REF" 2>/dev/null || true)"
  [[ -z "$base" ]] && return 0
  git diff --name-only "$base" "$REMOTE_REF" 2>/dev/null | sed 's#\\#/#g' | sort -u
}

local_committed_paths() {
  local base
  base="$(git merge-base HEAD "$REMOTE_REF" 2>/dev/null || true)"
  [[ -z "$base" ]] && return 0
  git diff --name-only "$base" HEAD 2>/dev/null | sed 's#\\#/#g' | sort -u
}

merge_would_conflict() {
  # Git 2.38+: 成功=きれいに merge 可、非0=衝突など
  if git merge-tree --write-tree HEAD "$REMOTE_REF" >/dev/null 2>&1; then
    return 1
  fi
  return 0
}

# overlap / conflict risk → sets RISK=1, RISK_FILES, RISK_REASONS
assess_conflict_risk() {
  RISK=0
  RISK_FILES=""
  RISK_REASONS=""
  local incoming overlap_dirty overlap_committed
  incoming="$(incoming_paths)"
  [[ -z "$incoming" && "$behind" -eq 0 ]] && return 0

  overlap_dirty="$(comm -12 <(dirty_paths) <(printf '%s\n' "$incoming") 2>/dev/null || true)"
  if [[ -n "${overlap_dirty// }" ]]; then
    RISK=1
    RISK_FILES="$(printf '%s\n%s\n' "$RISK_FILES" "$overlap_dirty")"
    RISK_REASONS="${RISK_REASONS}未コミットの修正と、取り込む ${MAIN_BRANCH} 側の変更が同じファイルにあります。"
    RISK_REASONS+=$'\n'
  fi

  overlap_committed="$(comm -12 <(local_committed_paths) <(printf '%s\n' "$incoming") 2>/dev/null || true)"
  if [[ -n "${overlap_committed// }" ]]; then
    RISK=1
    RISK_FILES="$(printf '%s\n%s\n' "$RISK_FILES" "$overlap_committed")"
    RISK_REASONS="${RISK_REASONS}この枝で直したファイルと、${MAIN_BRANCH} 側の変更が重なっています。"
    RISK_REASONS+=$'\n'
  fi

  if [[ "$behind" -gt 0 ]] && merge_would_conflict; then
    RISK=1
    RISK_REASONS="${RISK_REASONS}pull / merge すると、修正内容がぶつかります（自動ではきれいにくっつきません）。"
    RISK_REASONS+=$'\n'
  fi

  RISK_FILES="$(printf '%s\n' "$RISK_FILES" | sed '/^$/d' | sort -u)"
}

print_conflict_warning() {
  [[ "$RISK" -ne 1 ]] && return 0
  echo "" >&2
  bad "警告: いまの修正と GitHub の ${MAIN_BRANCH} をくっつけると、ぶつかりそうです。"
  while IFS= read -r reason; do
    [[ -z "$reason" ]] && continue
    bad "$reason"
  done <<< "$RISK_REASONS"
  if [[ -n "$RISK_FILES" ]]; then
    bad "重なりそうなファイル:"
    local n=0
    while IFS= read -r f; do
      [[ -z "$f" ]] && continue
      n=$((n + 1))
      if [[ $n -le 15 ]]; then
        bad "  - $f"
      fi
    done <<< "$RISK_FILES"
    local total
    total="$(printf '%s\n' "$RISK_FILES" | sed '/^$/d' | wc -l | tr -d ' ')"
    if [[ "$total" -gt 15 ]]; then
      bad "  ... ほか $((total - 15)) 件"
    fi
  fi
  warn "先にコミット／退避するか、状況を確認してから pull してください。ぶつかったまま無理に進めないでください。"
}

do_pull() {
  ok "git pull ${REMOTE} ${MAIN_BRANCH} を実行します..."
  if git pull "$REMOTE" "$MAIN_BRANCH"; then
    ok "pull が完了しました。"
    return 0
  fi
  bad "pull に失敗しました。修正内容とぶつかった可能性があります。"
  if [[ -n "$(git ls-files -u 2>/dev/null || true)" ]]; then
    bad "衝突（コンフリクト）が発生しています。解決するか、git merge --abort で取り消してください。"
  else
    bad "手動で状況を確認してください。"
  fi
  return 1
}

wait_ack() {
  echo "" >&2
  warn "$1"
  if [[ -t 0 ]]; then
    read -r -p "確認したら Enter を押してください（続ける場合）: " _ || true
  else
    warn "入力待ちできない環境のため、そのまま続行します。"
  fi
}

warn_if_unpushed() {
  # main 上で origin/main より進んでいる＝push 忘れ
  if [[ "$branch" == "$MAIN_BRANCH" && "$ahead" -gt 0 ]]; then
    warn "ローカルの ${MAIN_BRANCH} が GitHub より ${ahead} コミット進んでいます（未 push）。"
    warn "pull はしません。他端末へ引き継ぐ前に: git push origin ${MAIN_BRANCH}"
    return 0
  fi
  # 作業枝: 追跡枝または origin/<枝> との差で未 push を見る（main より進んでいるだけでは警告しない）
  local u_ahead=0
  if git rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1; then
    u_ahead="$(git rev-list --count '@{u}..HEAD' 2>/dev/null || echo 0)"
    if [[ "${u_ahead:-0}" -gt 0 ]]; then
      warn "この枝に未 push が ${u_ahead} コミットあります。他端末へ引き継ぐ前に: git push"
      return 0
    fi
  elif [[ "$branch" != "HEAD" && "$branch" != "(不明)" ]]; then
    if ! git rev-parse --verify "refs/remotes/${REMOTE}/${branch}" >/dev/null 2>&1; then
      if [[ "$ahead" -gt 0 ]]; then
        warn "この枝はまだ GitHub に無いかもしれません。作業後は: git push -u ${REMOTE} ${branch}"
      fi
      return 0
    fi
    u_ahead="$(git rev-list --count "${REMOTE}/${branch}..HEAD" 2>/dev/null || echo 0)"
    if [[ "${u_ahead:-0}" -gt 0 ]]; then
      warn "origin/${branch} へ未 push が ${u_ahead} コミットあります: git push"
    fi
  fi
}

counts
dirty=0
is_dirty && dirty=1

RISK=0
if [[ "$behind" -gt 0 || "$ahead" -gt 0 ]]; then
  assess_conflict_risk
  print_conflict_warning
fi

# --- pull 提案 / Agent 自動 ---
if [[ "$AGENT_MODE" -eq 1 ]]; then
  if [[ "$behind" -gt 0 && "$ahead" -eq 0 && "$dirty" -eq 0 && "$RISK" -eq 0 ]]; then
    bad "ローカルが GitHub の ${MAIN_BRANCH} より ${behind} コミット遅れています。"
    do_pull || true
    counts
    dirty=0
    is_dirty && dirty=1
    RISK=0
    if [[ "$behind" -gt 0 || "$ahead" -gt 0 ]]; then
      assess_conflict_risk
    fi
  elif [[ "$behind" -gt 0 && "$RISK" -eq 1 ]]; then
    bad "遅れていますが、pull すると修正とぶつかりそうです。自動では pull しません。"
  elif [[ "$behind" -gt 0 && "$dirty" -eq 1 ]]; then
    bad "遅れていますが、未コミットの変更があるため自動では pull しません。"
  elif [[ "$behind" -eq 0 ]]; then
    warn_if_unpushed
  fi
elif [[ "$PROMPT_PULL" -eq 1 && "$behind" -gt 0 && "$ahead" -eq 0 && "$dirty" -eq 0 && "$RISK" -eq 0 ]]; then
  echo "" >&2
  bad "ローカルが GitHub の ${MAIN_BRANCH} より ${behind} コミット遅れています。"
  warn "作業前に取り込むのが安全です（今の枝: ${branch}）。"
  answer="n"
  if [[ -t 0 ]]; then
    read -r -p "今すぐ「git pull ${REMOTE} ${MAIN_BRANCH}」しますか？ [Y/n] " answer || answer="n"
  else
    warn "入力できない環境のため、自動 pull はしません。"
  fi
  if [[ -z "$answer" || "$answer" =~ ^[Yy]$ ]]; then
    do_pull || true
    counts
    dirty=0
    is_dirty && dirty=1
    RISK=0
    if [[ "$behind" -gt 0 || "$ahead" -gt 0 ]]; then
      assess_conflict_risk
    fi
  else
    warn "pull をスキップしました。古い状態のまま作業するとぶつかりやすくなります。"
  fi
elif [[ "$PROMPT_PULL" -eq 1 && "$behind" -gt 0 && "$dirty" -eq 0 && "$RISK" -eq 1 ]]; then
  echo "" >&2
  bad "遅れていますが、pull すると修正とぶつかりそうです。"
  answer="n"
  if [[ -t 0 ]]; then
    read -r -p "それでも「git pull ${REMOTE} ${MAIN_BRANCH}」しますか？ 通常は n 推奨 [y/N] " answer || answer="n"
  fi
  if [[ "$answer" =~ ^[Yy]$ ]]; then
    do_pull || true
    counts
    dirty=0
    is_dirty && dirty=1
    RISK=0
    if [[ "$behind" -gt 0 || "$ahead" -gt 0 ]]; then
      assess_conflict_risk
    fi
  else
    warn "pull を見送りました。ぶつかるファイルを確認してから再開してください。"
  fi
elif [[ "$PROMPT_PULL" -eq 1 && "$behind" -gt 0 && "$dirty" -eq 1 ]]; then
  echo "" >&2
  bad "遅れていますが、未コミットの変更があるため自動では pull しません。"
  if [[ "$RISK" -eq 1 ]]; then
    print_conflict_warning
  else
    warn "先にコミットするか退避（stash）してから: git pull origin ${MAIN_BRANCH}"
  fi
fi

problems=()
if [[ "$behind" -gt 0 && "$ahead" -gt 0 ]]; then
  problems+=("ローカルと GitHub の ${MAIN_BRANCH} が分岐しています（遅れ ${behind} / 進み ${ahead}）。")
  problems+=("先に状況を確認し、必要なら pull や枝の整理をしてから直してください。")
elif [[ "$behind" -gt 0 ]]; then
  problems+=("ローカルが GitHub の ${MAIN_BRANCH} より ${behind} コミット遅れています。")
  problems+=("このまま直すと、他端末の修正とぶつかりやすくなります。")
  problems+=("対処: git pull origin ${MAIN_BRANCH}")
elif [[ "$ahead" -gt 0 && "$branch" == "$MAIN_BRANCH" ]]; then
  warn "ローカルの ${MAIN_BRANCH} が GitHub より ${ahead} コミット進んでいます（未 push）。"
  warn "他端末から続きを直す前に: git push origin ${MAIN_BRANCH}"
fi

if [[ "$dirty" -eq 1 && "$behind" -gt 0 ]]; then
  problems+=("未保存（未コミット）の変更もあります。pull の前にコミットするか退避してください。")
fi
if [[ "$RISK" -eq 1 ]]; then
  problems+=("修正内容と GitHub の ${MAIN_BRANCH} がぶつかりそうです（同じファイルの変更が重なる／merge 衝突の見込み）。")
fi

ok "今の枝: ${branch} / GitHub ${MAIN_BRANCH} との差: 遅れ=${behind} 進み=${ahead}"

should_stop=0
if [[ ${#problems[@]} -gt 0 || "$RISK" -eq 1 ]]; then
  should_stop=1
fi

if [[ "$should_stop" -eq 1 ]]; then
  echo "" >&2
  bad "警告: GitHub の ${MAIN_BRANCH} とローカルに齟齬／衝突の見込みがあります。"
  for line in "${problems[@]}"; do
    bad "$line"
  done
  echo "" >&2
  if [[ "$FAIL_IF_BEHIND" -eq 1 ]]; then
    bad "安全のためここで止めます（どうしても続行するなら SKIP_GIT_SYNC_CHECK=1）。"
    exit 1
  fi
  warn "このまま進むと、他端末で入れた修正とぶつかりやすくなります。"
  if [[ "$PAUSE_IF_BEHIND" -eq 1 ]]; then
    wait_ack "忘れ防止のため、一度ここで止まります。"
  else
    warn "続行します。修正やデプロイの前に上記を解消することを推奨します。"
  fi
  exit 0
fi

if [[ "$dirty" -eq 1 ]]; then
  warn "未コミットの変更があります（GitHub の ${MAIN_BRANCH} との遅れ・衝突見込みはありません）。"
else
  ok "GitHub の ${MAIN_BRANCH} と大きな遅れ・衝突見込みはありません。"
fi
exit 0
