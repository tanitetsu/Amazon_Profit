# GitHub の main とローカルのズレを確認する。
# Linux / Cloud Agent 向けの同仕様: .\check-git-sync.sh
# 使い方:
#   .\check-git-sync.ps1
#   .\check-git-sync.ps1 -FailIfBehind          # 遅れ／分岐／衝突見込みなら exit 1（デプロイ用）
#   .\check-git-sync.ps1 -PromptPull            # 遅れているとき pull するか尋ねる
#   .\check-git-sync.ps1 -PauseIfBehind         # まだ遅れ／衝突見込みのとき Enter 待ち
#   .\check-git-sync.ps1 -SkipFetch             # ネット無しで既存の origin/main と比較
# 環境変数 SKIP_GIT_SYNC_CHECK=1 なら何もせず成功終了

param(
  [string]$Remote = "origin",
  [string]$MainBranch = "main",
  [switch]$FailIfBehind,
  [switch]$PromptPull,
  [switch]$PauseIfBehind,
  [switch]$SkipFetch
)

$ErrorActionPreference = "Continue"
$Root = if ($PSScriptRoot) { $PSScriptRoot } else { Get-Location }
Set-Location -LiteralPath $Root

function Write-GitSyncOk {
  param([string]$Message)
  Write-Host "[git同期] $Message" -ForegroundColor Green
}

function Write-GitSyncWarn {
  param([string]$Message)
  Write-Host "[git同期] $Message" -ForegroundColor Yellow
}

function Write-GitSyncBad {
  param([string]$Message)
  Write-Host "[git同期] $Message" -ForegroundColor Red
}

function Get-GitSyncCounts {
  param([string]$RemoteRef)
  $b = [int](& git rev-list --count "HEAD..$RemoteRef" 2>$null)
  $a = [int](& git rev-list --count "$RemoteRef..HEAD" 2>$null)
  if ($b -lt 0) { $b = 0 }
  if ($a -lt 0) { $a = 0 }
  return @{ Behind = $b; Ahead = $a }
}

function Test-GitDirty {
  $dirty = @(& git status --porcelain 2>$null)
  return ($dirty.Count -gt 0)
}

function Get-GitDirtyPaths {
  $paths = @()
  $lines = @(& git status --porcelain -z 2>$null)
  # -z が使えない／空のとき行単位にフォールバック
  if ($lines.Count -eq 0) {
    foreach ($line in @(& git status --porcelain 2>$null)) {
      if ([string]::IsNullOrWhiteSpace($line) -or $line.Length -lt 4) { continue }
      $path = $line.Substring(3).Trim()
      if ($path -match ' -> ') {
        $path = ($path -split ' -> ', 2)[1]
      }
      if (-not [string]::IsNullOrWhiteSpace($path)) { $paths += $path.Replace('\', '/') }
    }
    return ,($paths | Select-Object -Unique)
  }
  # porcelain -z は1要素に連結されることがあるので NUL 分割
  $raw = (& git status --porcelain -z 2>$null) -join ""
  foreach ($entry in ($raw -split "`0")) {
    if ([string]::IsNullOrWhiteSpace($entry) -or $entry.Length -lt 4) { continue }
    $path = $entry.Substring(3).Trim()
    if ($path -match ' -> ') {
      $path = ($path -split ' -> ', 2)[1]
    }
    if (-not [string]::IsNullOrWhiteSpace($path)) { $paths += $path.Replace('\', '/') }
  }
  return ,($paths | Select-Object -Unique)
}

function Get-IncomingPathsFromMain {
  param([string]$RemoteRef)
  $base = (& git merge-base HEAD $RemoteRef 2>$null)
  if ([string]::IsNullOrWhiteSpace($base) -or $LASTEXITCODE -ne 0) {
    return @()
  }
  $paths = @(& git diff --name-only $base $RemoteRef 2>$null)
  return @($paths | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | ForEach-Object { $_.Replace('\', '/') })
}

function Get-LocalCommittedPathsVsMain {
  param([string]$RemoteRef)
  $base = (& git merge-base HEAD $RemoteRef 2>$null)
  if ([string]::IsNullOrWhiteSpace($base) -or $LASTEXITCODE -ne 0) {
    return @()
  }
  $paths = @(& git diff --name-only $base HEAD 2>$null)
  return @($paths | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | ForEach-Object { $_.Replace('\', '/') })
}

function Test-MergeWouldConflict {
  param([string]$RemoteRef)
  # Git 2.38+: 衝突時は exit code != 0
  $null = & git merge-tree --write-tree HEAD $RemoteRef 2>$null
  if ($LASTEXITCODE -ne 0) {
    return $true
  }
  # 古い Git 向けフォールバック
  $base = (& git merge-base HEAD $RemoteRef 2>$null)
  if ([string]::IsNullOrWhiteSpace($base)) {
    return $false
  }
  $preview = @(& git merge-tree $base HEAD $RemoteRef 2>$null)
  $text = ($preview -join "`n")
  if ($text -match 'changed in both|CONFLICT|<<<<<<|>>>>>>') {
    return $true
  }
  return $false
}

function Get-OverlapConflictRisk {
  param([string]$RemoteRef)
  $incoming = @(Get-IncomingPathsFromMain -RemoteRef $RemoteRef)
  if ($incoming.Count -eq 0) {
    return @{ HasRisk = $false; Files = @(); Reasons = @() }
  }
  $incomingSet = @{}
  foreach ($p in $incoming) { $incomingSet[$p] = $true }

  $overlap = @()
  $reasons = @()

  $dirtyPaths = @(Get-GitDirtyPaths)
  foreach ($p in $dirtyPaths) {
    if ($incomingSet.ContainsKey($p)) { $overlap += $p }
  }
  if ($overlap.Count -gt 0) {
    $reasons += "未コミットの修正と、取り込む $MainBranch 側の変更が同じファイルにあります。"
  }

  $localCommitted = @(Get-LocalCommittedPathsVsMain -RemoteRef $RemoteRef)
  $committedOverlap = @()
  foreach ($p in $localCommitted) {
    if ($incomingSet.ContainsKey($p)) { $committedOverlap += $p }
  }
  if ($committedOverlap.Count -gt 0) {
    $overlap += $committedOverlap
    $reasons += "この枝で直したファイルと、$MainBranch 側の変更が重なっています。"
  }

  $overlap = @($overlap | Select-Object -Unique)
  $mergeConflict = $false
  # コミット済みの取り込み衝突は merge-tree で判定（未コミットのみの重なりは上で検知）
  if ((Get-GitSyncCounts -RemoteRef $RemoteRef).Behind -gt 0) {
    $mergeConflict = Test-MergeWouldConflict -RemoteRef $RemoteRef
    if ($mergeConflict) {
      $reasons += "pull / merge すると、修正内容がぶつかります（自動ではきれいにくっつきません）。"
    }
  }

  $hasRisk = ($overlap.Count -gt 0) -or $mergeConflict
  return @{
    HasRisk = $hasRisk
    Files = $overlap
    MergeConflict = $mergeConflict
    Reasons = $reasons
  }
}

function Wait-GitSyncAck {
  param([string]$Message)
  Write-Host ""
  Write-GitSyncWarn $Message
  try {
    Read-Host "確認したら Enter を押してください（起動を続ける場合）" | Out-Null
  } catch {
    Write-GitSyncWarn "入力待ちできない環境のため、そのまま続行します。"
  }
}

function Write-ConflictRiskWarning {
  param($Risk, [string]$MainBranchName)
  if (-not $Risk.HasRisk) { return }
  Write-Host ""
  Write-GitSyncBad "警告: いまの修正と GitHub の $MainBranchName をくっつけると、ぶつかりそうです。"
  foreach ($reason in @($Risk.Reasons)) {
    Write-GitSyncBad $reason
  }
  $show = @($Risk.Files | Select-Object -First 15)
  if ($show.Count -gt 0) {
    Write-GitSyncBad "重なりそうなファイル:"
    foreach ($f in $show) {
      Write-GitSyncBad "  - $f"
    }
    if ($Risk.Files.Count -gt $show.Count) {
      Write-GitSyncBad ("  ... ほか {0} 件" -f ($Risk.Files.Count - $show.Count))
    }
  }
  Write-GitSyncWarn "先にコミット／退避するか、状況を確認してから pull してください。ぶつかったまま無理に進めないでください。"
}

if ($env:SKIP_GIT_SYNC_CHECK -eq "1") {
  Write-GitSyncWarn "SKIP_GIT_SYNC_CHECK=1 のため確認をスキップしました。"
  exit 0
}

$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if (-not $gitCmd) {
  Write-GitSyncWarn "git が見つかりません。同期確認をスキップします。"
  if ($FailIfBehind) { exit 1 }
  exit 0
}

$inside = & git rev-parse --is-inside-work-tree 2>$null
if ($LASTEXITCODE -ne 0 -or $inside -ne "true") {
  Write-GitSyncWarn "Git リポジトリではありません。同期確認をスキップします。"
  if ($FailIfBehind) { exit 1 }
  exit 0
}

$remoteRef = "$Remote/$MainBranch"

if (-not $SkipFetch) {
  Write-Host "[git同期] GitHub の $MainBranch を確認しています..." -ForegroundColor Cyan
  & git fetch $Remote $MainBranch 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Write-GitSyncWarn "fetch に失敗しました（ネット未接続など）。手元の $remoteRef で比較します。"
  }
}

& git rev-parse --verify $remoteRef 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-GitSyncWarn "$remoteRef がありません。remote 設定を確認してください。"
  if ($FailIfBehind) { exit 1 }
  exit 0
}

$branch = (& git rev-parse --abbrev-ref HEAD 2>$null)
if ([string]::IsNullOrWhiteSpace($branch)) { $branch = "(不明)" }

$counts = Get-GitSyncCounts -RemoteRef $remoteRef
$behind = $counts.Behind
$ahead = $counts.Ahead
$hasDirty = Test-GitDirty

# pull 前に衝突見込みを確認
$conflictRisk = @{ HasRisk = $false; Files = @(); MergeConflict = $false; Reasons = @() }
if ($behind -gt 0 -or $ahead -gt 0) {
  $conflictRisk = Get-OverlapConflictRisk -RemoteRef $remoteRef
  if ($conflictRisk.HasRisk) {
    Write-ConflictRiskWarning -Risk $conflictRisk -MainBranchName $MainBranch
  }
}

# 単純な「遅れのみ」かつ衝突見込みなしなら pull を提案
# 衝突見込みありのときは黙って Y で進めず、強い確認が必要
$canOfferPull = ($PromptPull -and $behind -gt 0 -and $ahead -eq 0 -and -not $hasDirty -and -not $conflictRisk.HasRisk)
$canOfferPullDespiteRisk = ($PromptPull -and $behind -gt 0 -and -not $hasDirty -and $conflictRisk.HasRisk)

if ($canOfferPull) {
  Write-Host ""
  Write-GitSyncBad "ローカルが GitHub の $MainBranch より $behind コミット遅れています。"
  Write-GitSyncWarn "作業前に取り込むのが安全です（今の枝: $branch）。"
  $answer = ""
  try {
    $answer = Read-Host "今すぐ「git pull $Remote $MainBranch」しますか？ [Y/n]"
  } catch {
    $answer = "n"
    Write-GitSyncWarn "入力できない環境のため、自動 pull はしません。"
  }
  if ([string]::IsNullOrWhiteSpace($answer) -or $answer -match '^[Yy]') {
    Write-Host "[git同期] git pull $Remote $MainBranch を実行します..." -ForegroundColor Cyan
    & git pull $Remote $MainBranch
    if ($LASTEXITCODE -ne 0) {
      Write-GitSyncBad "pull に失敗しました。修正内容とぶつかった可能性があります。"
      $unmerged = @(& git ls-files -u 2>$null)
      if ($unmerged.Count -gt 0) {
        Write-GitSyncBad "衝突（コンフリクト）が発生しています。解決するか、git merge --abort で取り消してください。"
      } else {
        Write-GitSyncBad "手動で状況を確認してください。"
      }
    } else {
      Write-GitSyncOk "pull が完了しました。"
      $counts = Get-GitSyncCounts -RemoteRef $remoteRef
      $behind = $counts.Behind
      $ahead = $counts.Ahead
      $hasDirty = Test-GitDirty
      $conflictRisk = Get-OverlapConflictRisk -RemoteRef $remoteRef
    }
  } else {
    Write-GitSyncWarn "pull をスキップしました。古い状態のまま作業するとぶつかりやすくなります。"
  }
} elseif ($canOfferPullDespiteRisk) {
  Write-Host ""
  Write-GitSyncBad "遅れていますが、pull すると修正とぶつかりそうです。"
  $answer = ""
  try {
    $answer = Read-Host "それでも「git pull $Remote $MainBranch」しますか？ 通常は n 推奨 [y/N]"
  } catch {
    $answer = "n"
  }
  if ($answer -match '^[Yy]') {
    Write-Host "[git同期] git pull $Remote $MainBranch を実行します..." -ForegroundColor Cyan
    & git pull $Remote $MainBranch
    if ($LASTEXITCODE -ne 0) {
      Write-GitSyncBad "pull に失敗しました。修正内容とぶつかった可能性が高いです。"
      $unmerged = @(& git ls-files -u 2>$null)
      if ($unmerged.Count -gt 0) {
        Write-GitSyncBad "衝突（コンフリクト）が発生しています。解決するか、git merge --abort で取り消してください。"
      }
    } else {
      Write-GitSyncOk "pull が完了しました（衝突なくくっついたようです）。"
      $counts = Get-GitSyncCounts -RemoteRef $remoteRef
      $behind = $counts.Behind
      $ahead = $counts.Ahead
      $hasDirty = Test-GitDirty
      $conflictRisk = Get-OverlapConflictRisk -RemoteRef $remoteRef
    }
  } else {
    Write-GitSyncWarn "pull を見送りました。ぶつかるファイルを確認してから再開してください。"
  }
} elseif ($PromptPull -and $behind -gt 0 -and $hasDirty) {
  Write-Host ""
  Write-GitSyncBad "遅れていますが、未コミットの変更があるため自動では pull しません。"
  if ($conflictRisk.HasRisk) {
    Write-ConflictRiskWarning -Risk $conflictRisk -MainBranchName $MainBranch
  } else {
    Write-GitSyncWarn "先にコミットするか退避（stash）してから: git pull origin $MainBranch"
  }
}

$problems = @()

if ($behind -gt 0 -and $ahead -gt 0) {
  $problems += "ローカルと GitHub の $MainBranch が分岐しています（遅れ $behind / 進み $ahead）。"
  $problems += "先に状況を確認し、必要なら pull や枝の整理をしてから直してください。"
} elseif ($behind -gt 0) {
  $problems += "ローカルが GitHub の $MainBranch より $behind コミット遅れています。"
  $problems += "このまま直すと、スマホ側の修正とぶつかりやすくなります。"
  $problems += "対処: git pull origin $MainBranch"
} elseif ($ahead -gt 0 -and $branch -eq $MainBranch) {
  Write-GitSyncWarn "ローカルの $MainBranch が GitHub より $ahead コミット進んでいます（未 push）。"
  Write-GitSyncWarn "他端末から続きを直す前に: git push origin $MainBranch"
}

if ($hasDirty -and $behind -gt 0) {
  $problems += "未保存（未コミット）の変更もあります。pull の前にコミットするか退避してください。"
}

if ($conflictRisk.HasRisk) {
  $problems += "修正内容と GitHub の $MainBranch がぶつかりそうです（同じファイルの変更が重なる／merge 衝突の見込み）。"
}

Write-Host "[git同期] 今の枝: $branch / GitHub $MainBranch との差: 遅れ=$behind 進み=$ahead" -ForegroundColor Cyan

$shouldStop = ($problems.Count -gt 0) -or $conflictRisk.HasRisk

if ($shouldStop) {
  Write-Host ""
  Write-GitSyncBad "警告: GitHub の $MainBranch とローカルに齟齬／衝突の見込みがあります。"
  foreach ($line in $problems) {
    Write-GitSyncBad $line
  }
  Write-Host ""
  if ($FailIfBehind) {
    Write-GitSyncBad "安全のためここで止めます（どうしても続行するなら -SkipGitSyncCheck）。"
    exit 1
  }
  Write-GitSyncWarn "このまま進むと、スマホで入れた修正とぶつかりやすくなります。"
  if ($PauseIfBehind) {
    Wait-GitSyncAck -Message "忘れ防止のため、一度ここで止まります。"
  } else {
    Write-GitSyncWarn "起動は続行します。修正やデプロイの前に上記を解消することを推奨します。"
  }
  exit 0
}

if ($hasDirty) {
  Write-GitSyncWarn "未コミットの変更があります（GitHub の $MainBranch との遅れ・衝突見込みはありません）。"
} else {
  Write-GitSyncOk "GitHub の $MainBranch と大きな遅れ・衝突見込みはありません。"
}
exit 0
