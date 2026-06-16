#!/bin/bash
# Push OAuth/session secrets from a local .env.<environment> file into AWS Secrets Manager.
# Avoids copy-pasting secret values into terminal commands (shell history, transcription errors).
#
# Usage:
#   ./scripts/push-secrets.sh staging      # reads .env.staging
#   ./scripts/push-secrets.sh production   # reads .env.production
#
# Expected file format (.env.staging / .env.production):
#   GOOGLE_CLIENT_ID=...
#   GOOGLE_CLIENT_SECRET=...
#   GITHUB_CLIENT_ID=...
#   GITHUB_CLIENT_SECRET=...
#   SESSION_SECRET=...

set -euo pipefail

ENV=${1:-}
REGION="ap-southeast-2"

if [[ -z "$ENV" ]]; then
  echo "Usage: $0 [staging|production]"
  exit 1
fi

ENV_FILE=".env.${ENV}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "✗ ${ENV_FILE} not found. Create it with GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, SESSION_SECRET"
  exit 1
fi

# Load the env file into this shell (does not print values)
set -a
source "$ENV_FILE"
set +a

for var in GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET GITHUB_CLIENT_ID GITHUB_CLIENT_SECRET SESSION_SECRET; do
  if [[ -z "${!var:-}" ]]; then
    echo "✗ ${var} is missing or empty in ${ENV_FILE}"
    exit 1
  fi
done

SECRET_NAME="bpel2orkes/${ENV}/oauth"
SECRET_JSON=$(cat <<EOF
{
  "GOOGLE_CLIENT_ID": "${GOOGLE_CLIENT_ID}",
  "GOOGLE_CLIENT_SECRET": "${GOOGLE_CLIENT_SECRET}",
  "GITHUB_CLIENT_ID": "${GITHUB_CLIENT_ID}",
  "GITHUB_CLIENT_SECRET": "${GITHUB_CLIENT_SECRET}",
  "SESSION_SECRET": "${SESSION_SECRET}"
}
EOF
)

echo "→ Pushing secrets to ${SECRET_NAME}..."

if aws secretsmanager describe-secret --region "$REGION" --secret-id "$SECRET_NAME" >/dev/null 2>&1; then
  aws secretsmanager put-secret-value \
    --region "$REGION" \
    --secret-id "$SECRET_NAME" \
    --secret-string "$SECRET_JSON" >/dev/null
  echo "✓ Updated existing secret ${SECRET_NAME}"
else
  aws secretsmanager create-secret \
    --region "$REGION" \
    --name "$SECRET_NAME" \
    --secret-string "$SECRET_JSON" >/dev/null
  echo "✓ Created new secret ${SECRET_NAME}"
fi

echo ""
echo "Next: redeploy ${ENV} so ECS picks up the new secret values:"
echo "  ./scripts/deploy.sh ${ENV}"
