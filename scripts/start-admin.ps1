$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

# GitHub の main とローカルのズレを確認（SKIP_GIT_SYNC_CHECK=1 で省略可）
# 手動起動時: 遅れていれば pull を尋ね、まだ遅れなら Enter 待ち（忘れ防止）
# Agent 起動（START_ADMIN_NO_PAUSE=1）: 警告のみ（入力待ちしない）
$gitSyncScript = Join-Path -Path (Get-Location) -ChildPath "check-git-sync.ps1"
if (Test-Path -LiteralPath $gitSyncScript) {
  try {
    $psExe = Join-Path $PSHOME "powershell.exe"
    if (-not (Test-Path -LiteralPath $psExe)) { $psExe = "powershell.exe" }
    $syncArgs = @(
      "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $gitSyncScript
    )
    if ($env:START_ADMIN_NO_PAUSE -ne "1") {
      $syncArgs += "-PromptPull"
      $syncArgs += "-PauseIfBehind"
    }
    $p = Start-Process -FilePath $psExe -ArgumentList $syncArgs -Wait -PassThru -NoNewWindow
    if ($null -ne $p -and $p.ExitCode -ne 0 -and $p.ExitCode -ne $null) {
      # 警告のみの想定（FailIfBehind なし）。異常時も起動は続行
    }
  } catch {
    Write-Host "[git同期] 確認スクリプトの実行に失敗しました: $_" -ForegroundColor Yellow
  }
  Write-Host ""
}

$Port = 5055
$Url = "http://127.0.0.1:$Port/"
$ChromeUserDataDir = Join-Path $PWD ".chrome-admin-profile"

function Test-PortInUse([int]$Port) {
  $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  return $null -ne $conn
}

function Stop-ListenerOnPort([int]$Port) {
  $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  foreach ($c in $conns) {
    $procId = $c.OwningProcess
    if ($procId) {
      try { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue } catch {}
    }
  }
}

function Get-ChromePath {
  $candidates = @(
    "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
  )
  foreach ($p in $candidates) {
    if ($p -and (Test-Path -LiteralPath $p)) { return $p }
  }
  return $null
}

function Start-ChromeWhenReady {
  param(
    [string]$Url,
    [int]$Port,
    [string]$ChromePath,
    [string]$UserDataDir,
    [int]$TimeoutSeconds = 40
  )
  Start-Job -ScriptBlock {
    param($Url, $Port, $ChromePath, $UserDataDir, $TimeoutSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
      $listening = $false
      try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        $listening = $null -ne $conn
      } catch {}
      if ($listening) {
        if ($ChromePath) {
          if (-not (Test-Path -LiteralPath $UserDataDir)) {
            New-Item -ItemType Directory -Path $UserDataDir -Force | Out-Null
          }
          $dir = $UserDataDir.Replace('"', '')
          $target = $Url.Replace('"', '')
          $argString = @(
            "--user-data-dir=`"$dir`"",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            "`"$target`""
          ) -join " "
          Start-Process -FilePath $ChromePath -ArgumentList $argString | Out-Null
        } else {
          Start-Process $Url | Out-Null
        }
        return
      }
      Start-Sleep -Milliseconds 300
    }
  } -ArgumentList $Url, $Port, $ChromePath, $UserDataDir, $TimeoutSeconds | Out-Null
}

if (-not (Test-Path .\.venv\Scripts\python.exe)) {
  Write-Host "Create venv first: python -m venv .venv"
  exit 1
}

if (Test-PortInUse -Port $Port) {
  Write-Host "Port $Port is in use. Stopping listener for restart..." -ForegroundColor Yellow
  Stop-ListenerOnPort -Port $Port
  $deadline = (Get-Date).AddSeconds(8)
  while ((Get-Date) -lt $deadline -and (Test-PortInUse -Port $Port)) {
    Start-Sleep -Milliseconds 250
  }
  if (Test-PortInUse -Port $Port) {
    Write-Host "Could not free port $Port. Ctrl+C the existing Admin and retry." -ForegroundColor Red
    exit 1
  }
  Write-Host "Port $Port freed." -ForegroundColor Green
}

.\.venv\Scripts\python.exe -m pip install -q -r requirements.txt -r requirements-admin.txt

$chrome = Get-ChromePath
if ($chrome) {
  Write-Host "Will open Chrome when ready: $Url" -ForegroundColor Cyan
} else {
  Write-Host "Chrome not found; will open default browser: $Url" -ForegroundColor Yellow
}
Start-ChromeWhenReady -Url $Url -Port $Port -ChromePath $chrome -UserDataDir $ChromeUserDataDir

Write-Host "Admin UI: $Url"

# Local admin should read the same admin-bucket config / poll runs as Cloud Run.
if (-not $env:APP_CONFIG_GCS_URI -and -not $env:USERS_CONFIG_GCS_URI) {
  $proj = ""
  try { $proj = (& gcloud config get-value project 2>$null).Trim() } catch {}
  if ($proj) {
    $uri = "gs://$proj-amazon-profit-admin/config/app_config.json"
    $env:APP_CONFIG_GCS_URI = $uri
    $env:USERS_CONFIG_GCS_URI = $uri
    Write-Host "APP_CONFIG_GCS_URI=$uri" -ForegroundColor DarkGray
  }
}

.\.venv\Scripts\python.exe app.py
