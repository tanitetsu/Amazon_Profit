#!/usr/bin/env bash
# Deploy admin + public OAuth Cloud Run services (Linux / Cloud Agent).
# Mirrors scripts/deploy-admin.ps1. Does NOT allow unauthenticated admin access.
#
# Usage:
#   ./scripts/deploy-admin.sh
#   ./scripts/deploy-admin.sh --project-id positive-design-480606-c7 --skip-iap
#   ./scripts/deploy-admin.sh --dry-run
#
# Cloud Agent: only from a clean origin/main checkout, when the user asked to deploy.
# Never prints secret JSON. Existing GCS secrets are kept (not regenerated).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PROJECT_ID="${GCP_PROJECT_ID:-positive-design-480606-c7}"
REGION="asia-northeast1"
SERVICE_NAME="amazon-profit-viewer"
SA_NAME="amazon-profit-admin"
REPO_NAME="amazon-profit"
USERS_GCS_OBJECT="config/app_config.json"
BUCKET_NAME=""
IAP_USER="26964u@gmail.com"
PUBLIC_BASE_URL=""
OAUTH_SERVICE_NAME="amazon-profit-oauth"
SKIP_BUILD=0
SKIP_IAP=0
SKIP_OAUTH=0
SKIP_GIT_SYNC=0
ALLOW_NON_MAIN=0
ALLOW_DIRTY=0
DRY_RUN=0

usage() {
  sed -n '2,14p' "$0"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-id) PROJECT_ID="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --service-name) SERVICE_NAME="$2"; shift 2 ;;
    --bucket-name) BUCKET_NAME="$2"; shift 2 ;;
    --public-base-url) PUBLIC_BASE_URL="$2"; shift 2 ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    --skip-iap) SKIP_IAP=1; shift ;;
    --skip-oauth) SKIP_OAUTH=1; shift ;;
    --skip-git-sync-check) SKIP_GIT_SYNC=1; shift ;;
    --allow-non-main) ALLOW_NON_MAIN=1; shift ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$BUCKET_NAME" ]]; then
  BUCKET_NAME="${PROJECT_ID}-amazon-profit-admin"
fi

SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/admin:latest"
USERS_GCS_URI="gs://${BUCKET_NAME}/${USERS_GCS_OBJECT}"
OPERATOR_TOKEN_URI="gs://${BUCKET_NAME}/secrets/operator_token.json"
OAUTH_CLIENT_URI="gs://${BUCKET_NAME}/secrets/oauth_client.json"
INVITE_SECRET_URI="gs://${BUCKET_NAME}/secrets/gmail_invite_secret.txt"
MAIL_POLL_SECRET_URI="gs://${BUCKET_NAME}/secrets/mail_poll_secret.txt"

log() { printf '%s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

gcloud_ok() {
  gcloud "$@" >/dev/null 2>&1
}

ensure_gcloud() {
  if command -v gcloud >/dev/null 2>&1; then
    return 0
  fi
  local sdk_dir="${HOME}/google-cloud-sdk"
  if [[ -x "${sdk_dir}/bin/gcloud" ]]; then
    # shellcheck disable=SC1091
    source "${sdk_dir}/path.bash.inc" 2>/dev/null || export PATH="${sdk_dir}/bin:${PATH}"
    return 0
  fi
  log "Installing Google Cloud SDK into ${sdk_dir} ..."
  local tarball="https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-x86_64.tar.gz"
  local tmp
  tmp="$(mktemp -d)"
  curl -fsSL "$tarball" -o "${tmp}/gcloud.tgz"
  tar -C "$HOME" -xzf "${tmp}/gcloud.tgz"
  rm -rf "$tmp"
  "${sdk_dir}/install.sh" --quiet --usage-reporting false --path-update false
  export PATH="${sdk_dir}/bin:${PATH}"
  command -v gcloud >/dev/null 2>&1 || die "gcloud install finished but gcloud is not on PATH"
}

activate_deploy_sa() {
  local path
  path="$(
    python3 - <<'PY'
from app.gcs_credentials import resolve_deploy_credentials_path
p = resolve_deploy_credentials_path()
print(p or "")
PY
  )"
  [[ -n "$path" ]] || die "No deploy SA. Set GCP_DEPLOY_CREDENTIALS or AIC_GCS_CREDENTIALS (path or JSON). Do not paste JSON in chat."
  [[ -f "$path" ]] || die "Resolved deploy credentials path is not a file"
  gcloud auth activate-service-account --key-file="$path" --quiet
  export GOOGLE_APPLICATION_CREDENTIALS="$path"
  export CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE="$path"
  log "Activated deploy service account from resolved credentials file (path not logged)."
}

assert_git_ready() {
  if [[ "$SKIP_GIT_SYNC" -eq 1 || "${SKIP_GIT_SYNC_CHECK:-}" == "1" ]]; then
    warn "Git sync check skipped."
    return 0
  fi
  ./check-git-sync.sh --fail-if-behind
  git fetch origin main >/dev/null 2>&1 || true
  local head main_sha
  head="$(git rev-parse HEAD)"
  main_sha="$(git rev-parse origin/main)"
  if [[ "$head" != "$main_sha" && "$ALLOW_NON_MAIN" -ne 1 ]]; then
    die "Deploy only from origin/main (HEAD=${head:0:7} main=${main_sha:0:7} branch=$(git rev-parse --abbrev-ref HEAD)). Merge first, or pass --allow-non-main only if explicitly requested."
  fi
  if [[ "$ALLOW_DIRTY" -ne 1 ]]; then
    if [[ -n "$(git status --porcelain)" ]]; then
      die "Working tree is not clean. Commit/stash first, or pass --allow-dirty only if explicitly requested."
    fi
  fi
}

gcs_exists() {
  gcloud storage ls "$1" --project "$PROJECT_ID" >/dev/null 2>&1
}

# Write secret bytes to $2. Never echo them.
load_or_seed_secret() {
  local uri="$1"
  local local_path="$2"
  local dest="$3"
  local gcs=0
  local loc=0
  gcs_exists "$uri" && gcs=1
  [[ -f "$local_path" ]] && loc=1
  local source
  source="$(
    python3 - "$gcs" "$loc" <<'PY'
import sys
from app.deploy_secrets import pick_secret_source
print(pick_secret_source(gcs_exists=sys.argv[1] == "1", local_exists=sys.argv[2] == "1"))
PY
  )"
  mkdir -p "$(dirname "$dest")"
  case "$source" in
    gcs)
      log "Using existing ${uri} (not rotating)."
      gcloud storage cp "$uri" "$dest" --project "$PROJECT_ID" >/dev/null
      ;;
    local)
      log "Uploading local $(basename "$local_path") -> ${uri}"
      gcloud storage cp "$local_path" "$uri" --project "$PROJECT_ID" >/dev/null
      cp "$local_path" "$dest"
      ;;
    generate)
      log "Generating new $(basename "$uri") (GCS and local were missing)."
      python3 - "$dest" <<'PY'
import secrets, sys
from pathlib import Path
Path(sys.argv[1]).write_text(secrets.token_urlsafe(32), encoding="ascii")
PY
      gcloud storage cp "$dest" "$uri" --project "$PROJECT_ID" >/dev/null
      ;;
    *) die "unknown secret source" ;;
  esac
}

read_secret_file() {
  python3 - "$1" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).read_text(encoding="utf-8").strip(), end="")
PY
}

assert_git_ready
ensure_gcloud
activate_deploy_sa

log "Project: ${PROJECT_ID}"
log "Region:  ${REGION}"
log "Service: ${SERVICE_NAME}"
log "SA:      ${SA_EMAIL}"
log "Image:   ${IMAGE}"
log "Users:   ${USERS_GCS_URI}"

if [[ "$DRY_RUN" -eq 1 ]]; then
  log "Dry run: git and credentials OK. Not enabling APIs, building, or deploying."
  exit 0
fi

gcloud config set project "$PROJECT_ID" >/dev/null

log "Enabling APIs..."
gcloud services enable \
  run.googleapis.com \
  iap.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  storage.googleapis.com \
  iam.googleapis.com \
  drive.googleapis.com \
  sheets.googleapis.com \
  cloudresourcemanager.googleapis.com \
  --project "$PROJECT_ID"

if ! gcloud_ok iam service-accounts describe "$SA_EMAIL" --project "$PROJECT_ID"; then
  log "Creating service account ${SA_EMAIL} ..."
  gcloud iam service-accounts create "$SA_NAME" \
    --display-name "Amazon profit admin (Drive/Sheets/GCS)" \
    --project "$PROJECT_ID"
fi

gcloud_ok projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:${SA_EMAIL}" \
  --role "roles/storage.objectAdmin" \
  --condition=None \
  --quiet || true

log "Granting ${SA_EMAIL} roles/iap.admin (IAP roster sync)..."
gcloud_ok projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:${SA_EMAIL}" \
  --role "roles/iap.admin" \
  --condition=None \
  --quiet || true

if ! gcloud_ok artifacts repositories describe "$REPO_NAME" --location "$REGION" --project "$PROJECT_ID"; then
  log "Creating Artifact Registry repo ${REPO_NAME} ..."
  gcloud artifacts repositories create "$REPO_NAME" \
    --repository-format=docker \
    --location "$REGION" \
    --description "amazon-profit-viewer images" \
    --project "$PROJECT_ID"
fi

if ! gcloud_ok storage buckets describe "gs://${BUCKET_NAME}" --project "$PROJECT_ID"; then
  log "Creating bucket gs://${BUCKET_NAME} ..."
  gcloud storage buckets create "gs://${BUCKET_NAME}" \
    --project "$PROJECT_ID" \
    --location "$REGION" \
    --uniform-bucket-level-access
fi

USERS_LOCAL="config/app_config.json"
if [[ -f "$USERS_LOCAL" ]]; then
  log "Syncing config/app_config.json -> ${USERS_GCS_URI}"
  gcloud storage cp "$USERS_LOCAL" "$USERS_GCS_URI" --project "$PROJECT_ID"
elif gcs_exists "$USERS_GCS_URI"; then
  log "No local config/app_config.json; keeping existing ${USERS_GCS_URI}"
else
  die "config/app_config.json missing and GCS object not found: ${USERS_GCS_URI}"
fi

if [[ -f secrets/operator_token.json ]]; then
  log "Syncing operator OAuth token -> ${OPERATOR_TOKEN_URI}"
  gcloud storage cp secrets/operator_token.json "$OPERATOR_TOKEN_URI" --project "$PROJECT_ID"
elif gcs_exists "$OPERATOR_TOKEN_URI"; then
  log "Keeping existing ${OPERATOR_TOKEN_URI}"
else
  warn "operator token missing locally and in GCS; consent mail needs ${OPERATOR_TOKEN_URI}"
fi

if [[ -f secrets/oauth_client.json ]]; then
  log "Syncing OAuth client -> ${OAUTH_CLIENT_URI}"
  gcloud storage cp secrets/oauth_client.json "$OAUTH_CLIENT_URI" --project "$PROJECT_ID"
elif gcs_exists "$OAUTH_CLIENT_URI"; then
  log "Keeping existing ${OAUTH_CLIENT_URI}"
else
  warn "OAuth client missing locally and in GCS; consent callback needs ${OAUTH_CLIENT_URI}"
fi

SECRET_DIR="$(mktemp -d)"
trap 'rm -rf "$SECRET_DIR"' EXIT
load_or_seed_secret "$INVITE_SECRET_URI" "secrets/gmail_invite_secret.txt" "${SECRET_DIR}/gmail_invite_secret.txt"
load_or_seed_secret "$MAIL_POLL_SECRET_URI" "secrets/mail_poll_secret.txt" "${SECRET_DIR}/mail_poll_secret.txt"
INVITE_SECRET="$(read_secret_file "${SECRET_DIR}/gmail_invite_secret.txt")"
MAIL_POLL_SECRET="$(read_secret_file "${SECRET_DIR}/mail_poll_secret.txt")"

gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

if [[ "$SKIP_BUILD" -ne 1 ]]; then
  log "Building and pushing image via Cloud Build..."
  gcloud builds submit --tag "$IMAGE" --project "$PROJECT_ID" .
fi

OAUTH_URL=""
if [[ "$SKIP_OAUTH" -ne 1 ]]; then
  log "Deploying public OAuth service ${OAUTH_SERVICE_NAME} ..."
  local_oauth_env="$(
    python3 - "$USERS_GCS_URI" "$OPERATOR_TOKEN_URI" "$INVITE_SECRET" "$MAIL_POLL_SECRET" "$OAUTH_CLIENT_URI" <<'PY'
import sys
pairs = [
    "APP_SURFACE=public",
    "ADMIN_USE_ADC=1",
    f"USERS_CONFIG_GCS_URI={sys.argv[1]}",
    f"APP_CONFIG_GCS_URI={sys.argv[1]}",
    f"OPERATOR_TOKEN_GCS_URI={sys.argv[2]}",
    f"GMAIL_INVITE_SECRET={sys.argv[3]}",
    f"MAIL_POLL_SECRET={sys.argv[4]}",
    f"OAUTH_CLIENT_GCS_URI={sys.argv[5]}",
    "MAIL_POLL_MAX_WORKERS=1",
    "MAIL_INGEST_MAX_PER_POLL=25",
    "MAIL_INGEST_BUDGET_SEC=480",
]
print(",".join(pairs))
PY
  )"
  gcloud run deploy "$OAUTH_SERVICE_NAME" \
    --image "$IMAGE" \
    --region "$REGION" \
    --platform managed \
    --service-account "$SA_EMAIL" \
    --allow-unauthenticated \
    --set-env-vars "$local_oauth_env" \
    --memory 2Gi \
    --cpu 1 \
    --timeout 900 \
    --concurrency 1 \
    --max-instances 2 \
    --project "$PROJECT_ID"
  OAUTH_URL="$(gcloud run services describe "$OAUTH_SERVICE_NAME" --region "$REGION" --project "$PROJECT_ID" --format 'value(status.url)')"
  if [[ -z "$PUBLIC_BASE_URL" ]]; then
    PUBLIC_BASE_URL="$OAUTH_URL"
  fi
  gcloud run services update "$OAUTH_SERVICE_NAME" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --update-env-vars "PUBLIC_BASE_URL=${PUBLIC_BASE_URL}" >/dev/null
fi

log "Deploying admin Cloud Run (IAP; no unauthenticated access)..."
ADMIN_ENV="$(
  python3 - "$USERS_GCS_URI" "$OPERATOR_TOKEN_URI" "$INVITE_SECRET" "$MAIL_POLL_SECRET" "$OAUTH_CLIENT_URI" "$PROJECT_ID" "$REGION" "$PUBLIC_BASE_URL" <<'PY'
import sys
pairs = [
    "APP_SURFACE=admin",
    "ADMIN_USE_ADC=1",
    f"USERS_CONFIG_GCS_URI={sys.argv[1]}",
    f"APP_CONFIG_GCS_URI={sys.argv[1]}",
    f"OPERATOR_TOKEN_GCS_URI={sys.argv[2]}",
    f"GMAIL_INVITE_SECRET={sys.argv[3]}",
    f"MAIL_POLL_SECRET={sys.argv[4]}",
    f"OAUTH_CLIENT_GCS_URI={sys.argv[5]}",
    f"GCP_PROJECT_ID={sys.argv[6]}",
    f"IAP_REGION={sys.argv[7]}",
    "IAP_CLOUD_RUN_SERVICES=ai-cripping-data-viewer",
    "IAP_AUTO_GRANT=1",
]
if sys.argv[8]:
    pairs.append(f"PUBLIC_BASE_URL={sys.argv[8]}")
print(",".join(pairs))
PY
)"
gcloud run deploy "$SERVICE_NAME" \
  --image "$IMAGE" \
  --region "$REGION" \
  --platform managed \
  --service-account "$SA_EMAIL" \
  --no-allow-unauthenticated \
  --set-env-vars "$ADMIN_ENV" \
  --memory 2Gi \
  --cpu 1 \
  --timeout 900 \
  --concurrency 1 \
  --max-instances 2 \
  --project "$PROJECT_ID"

if [[ "$SKIP_IAP" -ne 1 ]]; then
  log "Enabling IAP on Cloud Run service..."
  if ! gcloud_ok run services update "$SERVICE_NAME" --region "$REGION" --iap --project "$PROJECT_ID"; then
    warn "Retrying IAP enable with gcloud beta..."
    gcloud_ok beta run services update "$SERVICE_NAME" --region "$REGION" --iap --project "$PROJECT_ID" \
      || warn "Could not enable IAP via CLI. Enable in Console if needed."
  fi
  log "Granting IAP / invoker access to ${IAP_USER} ..."
  gcloud_ok projects add-iam-policy-binding "$PROJECT_ID" \
    --member "user:${IAP_USER}" \
    --role "roles/iap.httpsResourceAccessor" \
    --condition=None \
    --quiet || true
  gcloud_ok run services add-iam-policy-binding "$SERVICE_NAME" \
    --region "$REGION" \
    --member "user:${IAP_USER}" \
    --role "roles/run.invoker" \
    --project "$PROJECT_ID" \
    --quiet || true
fi

URL="$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" --project "$PROJECT_ID" --format 'value(status.url)')"
log ""
log "Admin (IAP):  ${URL}"
if [[ -n "$OAUTH_URL" ]]; then
  log "OAuth public: ${OAUTH_URL}"
  log "PUBLIC_BASE_URL=${PUBLIC_BASE_URL}"
  log ""
  log "Register this redirect URI on a Web OAuth client:"
  log "  ${PUBLIC_BASE_URL}/oauth/gmail/callback"
fi
log ""
log "Manual steps (first time):"
log "  1. Share Drive folder 'User_Acounting' with ${SA_EMAIL} as Editor (from 26964u@gmail.com)."
log "  2. Open the admin URL in Chrome, sign in as ${IAP_USER}."
log "  3. Confirm user list loads (GET /api/users)."
