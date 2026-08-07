# Deploy admin UI to Cloud Run (IAP + runtime SA). Does NOT allow unauthenticated access.
# Prerequisites: gcloud CLI authenticated, billing enabled.
#
# Usage:
#   .\scripts\deploy-admin.ps1 -ProjectId "my-gcp-project"
#   .\scripts\deploy-admin.ps1 -ProjectId "my-gcp-project" -Region "asia-northeast1" -SkipBuild
#
# After first deploy: share Drive folder User_Acounting with the runtime SA as Editor
# (see docs/operations.md).

param(
  [Parameter(Mandatory = $true)]
  [string]$ProjectId,

  [string]$Region = "asia-northeast1",
  [string]$ServiceName = "amazon-profit-viewer",
  [string]$SaName = "amazon-profit-admin",
  [string]$RepoName = "amazon-profit",
  [string]$UsersGcsObject = "config/app_config.json",
  [string]$BucketName = "",
  [string]$IapUser = "26964u@gmail.com",
  [string]$PublicBaseUrl = "",
  [string]$OauthServiceName = "amazon-profit-oauth",
  [switch]$SkipBuild,
  [switch]$SkipIap,
  [switch]$SkipOauth
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

if (-not $BucketName) {
  $BucketName = "$ProjectId-amazon-profit-admin"
}

$SaEmail = "$SaName@$ProjectId.iam.gserviceaccount.com"
$Image = "$Region-docker.pkg.dev/$ProjectId/$RepoName/admin:latest"
$UsersGcsUri = "gs://$BucketName/$UsersGcsObject"

function Assert-Gcloud {
  if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    throw "gcloud CLI not found. Install Google Cloud SDK and re-run."
  }
}

function Invoke-GcloudQuiet {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GcloudArgs)
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  & gcloud @GcloudArgs 2>$null | Out-Null
  $code = $LASTEXITCODE
  $ErrorActionPreference = $prev
  return ($code -eq 0)
}

Assert-Gcloud

Write-Host "Project: $ProjectId"
Write-Host "Region:  $Region"
Write-Host "Service: $ServiceName"
Write-Host "SA:      $SaEmail"
Write-Host "Image:   $Image"
Write-Host "Users:   $UsersGcsUri"

gcloud config set project $ProjectId | Out-Null

Write-Host "Enabling APIs..."
gcloud services enable `
  run.googleapis.com `
  iap.googleapis.com `
  artifactregistry.googleapis.com `
  cloudbuild.googleapis.com `
  storage.googleapis.com `
  iam.googleapis.com `
  drive.googleapis.com `
  sheets.googleapis.com `
  --project $ProjectId

if (-not (Invoke-GcloudQuiet iam service-accounts describe $SaEmail --project $ProjectId)) {
  Write-Host "Creating service account $SaEmail ..."
  gcloud iam service-accounts create $SaName `
    --display-name "Amazon profit admin (Drive/Sheets/GCS)" `
    --project $ProjectId
}

$deployer = (gcloud config get-value account 2>$null).Trim()
if ($deployer) {
  Write-Host "Allowing $deployer to deploy as $SaEmail ..."
  Invoke-GcloudQuiet iam service-accounts add-iam-policy-binding $SaEmail `
    --member "user:$deployer" `
    --role "roles/iam.serviceAccountUser" `
    --project $ProjectId `
    --quiet | Out-Null
}

# Runtime SA needs object admin on the app_config bucket
Invoke-GcloudQuiet projects add-iam-policy-binding $ProjectId `
  --member "serviceAccount:$SaEmail" `
  --role "roles/storage.objectAdmin" `
  --condition=None `
  --quiet | Out-Null

# Provision/deprovision auto-grants IAP on AI_Cripping Cloud Run (resource-level).
Write-Host "Granting $SaEmail roles/iap.admin (IAP roster sync)..."
Invoke-GcloudQuiet projects add-iam-policy-binding $ProjectId `
  --member "serviceAccount:$SaEmail" `
  --role "roles/iap.admin" `
  --condition=None `
  --quiet | Out-Null

# cloudresourcemanager for project number lookup in app/iap_access.py
Invoke-GcloudQuiet services enable cloudresourcemanager.googleapis.com --project $ProjectId

if (-not (Invoke-GcloudQuiet artifacts repositories describe $RepoName --location $Region --project $ProjectId)) {
  Write-Host "Creating Artifact Registry repo $RepoName ..."
  gcloud artifacts repositories create $RepoName `
    --repository-format=docker `
    --location $Region `
    --description "amazon-profit-viewer images" `
    --project $ProjectId
}

if (-not (Invoke-GcloudQuiet storage buckets describe "gs://$BucketName" --project $ProjectId)) {
  Write-Host "Creating bucket gs://$BucketName ..."
  gcloud storage buckets create "gs://$BucketName" `
    --project $ProjectId `
    --location $Region `
    --uniform-bucket-level-access
}

if (-not (Test-Path -LiteralPath "config\app_config.json")) {
  throw "config/app_config.json missing; cannot seed GCS"
}

$UsersLocalPath = (Resolve-Path "config\app_config.json").Path
Write-Host "Syncing config/app_config.json -> $UsersGcsUri"
gcloud storage cp $UsersLocalPath $UsersGcsUri --project $ProjectId

# Operator user OAuth (gmail.send)  Erequired for consent mail on Cloud Run (SA cannot send)
$OperatorTokenUri = "gs://$BucketName/secrets/operator_token.json"
$OperatorTokenLocal = "secrets\operator_token.json"
if (Test-Path $OperatorTokenLocal) {
  Write-Host "Syncing operator OAuth token -> $OperatorTokenUri"
  gcloud storage cp $OperatorTokenLocal $OperatorTokenUri --project $ProjectId
} else {
  Write-Host "WARN: $OperatorTokenLocal missing; consent mail needs $OperatorTokenUri" -ForegroundColor Yellow
}

$InviteSecretLocal = "secrets\gmail_invite_secret.txt"
if (-not (Test-Path $InviteSecretLocal)) {
  $bytes = New-Object byte[] 32
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  [Convert]::ToBase64String($bytes) | Set-Content -Path $InviteSecretLocal -NoNewline -Encoding ascii
}
$InviteSecret = (Get-Content $InviteSecretLocal -Raw).Trim()
gcloud storage cp $InviteSecretLocal "gs://$BucketName/secrets/gmail_invite_secret.txt" --project $ProjectId | Out-Null

$MailPollSecretLocal = "secrets\mail_poll_secret.txt"
if (-not (Test-Path $MailPollSecretLocal)) {
  $bytes = New-Object byte[] 32
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  [Convert]::ToBase64String($bytes) | Set-Content -Path $MailPollSecretLocal -NoNewline -Encoding ascii
}
$MailPollSecret = (Get-Content $MailPollSecretLocal -Raw).Trim()
gcloud storage cp $MailPollSecretLocal "gs://$BucketName/secrets/mail_poll_secret.txt" --project $ProjectId | Out-Null

$OauthClientUri = "gs://$BucketName/secrets/oauth_client.json"
$OauthClientLocal = "secrets\oauth_client.json"
if (Test-Path $OauthClientLocal) {
  Write-Host "Syncing OAuth client -> $OauthClientUri"
  gcloud storage cp $OauthClientLocal $OauthClientUri --project $ProjectId
} else {
  Write-Host "WARN: $OauthClientLocal missing; consent callback needs a Web OAuth client at $OauthClientUri" -ForegroundColor Yellow
}

gcloud auth configure-docker "$Region-docker.pkg.dev" --quiet

if (-not $SkipBuild) {
  Write-Host "Building and pushing image via Cloud Build..."
  gcloud builds submit --tag $Image --project $ProjectId .
}

# --- Public OAuth surface (end-user consent + mail-poll). Not IAP. ---
$OauthUrl = ""
if (-not $SkipOauth) {
  Write-Host "Deploying public OAuth service $OauthServiceName ..."
  $oauthEnv = @(
    "APP_SURFACE=public",
    "ADMIN_USE_ADC=1",
    "USERS_CONFIG_GCS_URI=$UsersGcsUri",
    "APP_CONFIG_GCS_URI=$UsersGcsUri",
    "OPERATOR_TOKEN_GCS_URI=$OperatorTokenUri",
    "GMAIL_INVITE_SECRET=$InviteSecret",
    "MAIL_POLL_SECRET=$MailPollSecret",
    "OAUTH_CLIENT_GCS_URI=$OauthClientUri",
    "MAIL_POLL_MAX_WORKERS=1",
    "MAIL_INGEST_MAX_PER_POLL=25",
    "MAIL_INGEST_BUDGET_SEC=480"
  ) -join ","

  # Mail-poll loads Sheets/Gmail clients; 512Mi OOMs (~520 E40Mi observed).
  # Timeout must cover multi-user poll; keep >= Scheduler attemptDeadline.
  # Concurrency 1; max-instances 2 for consent while a poll holds one instance.
  # Overlapping polls are blocked by GCS mail_poll_lock (not max-instances alone).
  gcloud run deploy $OauthServiceName `
    --image $Image `
    --region $Region `
    --platform managed `
    --service-account $SaEmail `
    --allow-unauthenticated `
    --set-env-vars $oauthEnv `
    --memory 2Gi `
    --cpu 1 `
    --timeout 900 `
    --concurrency 1 `
    --max-instances 2 `
    --project $ProjectId

  $OauthUrl = gcloud run services describe $OauthServiceName --region $Region --project $ProjectId --format "value(status.url)"
  # Prefer run.app URL that users can open (mjqzkyqita style may also work)
  if (-not $PublicBaseUrl) {
    $PublicBaseUrl = $OauthUrl
  }
  gcloud run services update $OauthServiceName `
    --region $Region `
    --project $ProjectId `
    --update-env-vars "PUBLIC_BASE_URL=$PublicBaseUrl" | Out-Null
}

Write-Host "Deploying admin Cloud Run (IAP; no unauthenticated access)..."
$envPairs = @(
  "APP_SURFACE=admin",
  "ADMIN_USE_ADC=1",
  "USERS_CONFIG_GCS_URI=$UsersGcsUri",
    "APP_CONFIG_GCS_URI=$UsersGcsUri",
  "OPERATOR_TOKEN_GCS_URI=$OperatorTokenUri",
  "GMAIL_INVITE_SECRET=$InviteSecret",
  "MAIL_POLL_SECRET=$MailPollSecret",
  "OAUTH_CLIENT_GCS_URI=$OauthClientUri",
  "GCP_PROJECT_ID=$ProjectId",
  "IAP_REGION=$Region",
  "IAP_CLOUD_RUN_SERVICES=ai-cripping-data-viewer",
  "IAP_AUTO_GRANT=1"
)
if ($PublicBaseUrl) {
  $envPairs += "PUBLIC_BASE_URL=$PublicBaseUrl"
}
$envCsv = $envPairs -join ","

  # Provision can take several minutes (protections + Exclusive seed + IAP).
  # Must stay >= gunicorn --timeout in Dockerfile (900).
  # 512Mi OOMs during template copy/seed (~580Mi observed) ↁECloud Run 503.
  # Concurrency 1: admin adds are rare and memory-heavy; avoid parallel OOM.
  gcloud run deploy $ServiceName `
  --image $Image `
  --region $Region `
  --platform managed `
  --service-account $SaEmail `
  --no-allow-unauthenticated `
  --set-env-vars $envCsv `
  --memory 2Gi `
  --cpu 1 `
  --timeout 900 `
  --concurrency 1 `
  --max-instances 2 `
  --project $ProjectId

if (-not $SkipIap) {
  Write-Host "Enabling IAP on Cloud Run service..."
  $iapOk = $false
  if (Invoke-GcloudQuiet run services update $ServiceName --region $Region --iap --project $ProjectId) {
    $iapOk = $true
  } else {
    Write-Host "Retrying with gcloud beta..." -ForegroundColor Yellow
    if (Invoke-GcloudQuiet beta run services update $ServiceName --region $Region --iap --project $ProjectId) {
      $iapOk = $true
    }
  }
  if (-not $iapOk) {
    Write-Host "Could not enable IAP via CLI. Enable IAP for the Cloud Run service in Console, then grant roles/iap.httpsResourceAccessor to $IapUser." -ForegroundColor Yellow
  }

  Write-Host "Granting IAP / invoker access to $IapUser ..."
  Invoke-GcloudQuiet projects add-iam-policy-binding $ProjectId `
    --member "user:$IapUser" `
    --role "roles/iap.httpsResourceAccessor" `
    --condition=None `
    --quiet | Out-Null

  Invoke-GcloudQuiet run services add-iam-policy-binding $ServiceName `
    --region $Region `
    --member "user:$IapUser" `
    --role "roles/run.invoker" `
    --project $ProjectId `
    --quiet | Out-Null
}

$url = gcloud run services describe $ServiceName --region $Region --project $ProjectId --format "value(status.url)"
Write-Host ""
Write-Host "Admin (IAP):  $url"
if ($OauthUrl) {
  Write-Host "OAuth public: $OauthUrl"
  Write-Host "PUBLIC_BASE_URL=$PublicBaseUrl"
  Write-Host ""
  Write-Host "Register this redirect URI on a *Web* OAuth client (Desktop client cannot):"
  Write-Host "  $PublicBaseUrl/oauth/gmail/callback"
  Write-Host "Then upload the Web client JSON to $OauthClientUri (and secrets/oauth_client.json)."
}
Write-Host ""
Write-Host "Manual steps (first time):"
Write-Host "  1. Share Drive folder 'User_Acounting' with $SaEmail as Editor (from 26964u@gmail.com)."
Write-Host "  2. Open $url in Chrome, sign in as $IapUser."
Write-Host "  3. Confirm user list loads (GET /api/users)."
Write-Host "  4. Resend consent mail so the link uses the public OAuth origin."
Write-Host ""
Write-Host "Local admin remains: .\scripts\start-admin.ps1"
