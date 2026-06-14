#!/bin/bash
# Manual deploy script — builds, pushes to ECR, forces ECS redeploy
# Usage:
#   ./scripts/deploy.sh staging
#   ./scripts/deploy.sh production

set -euo pipefail

ENV=${1:-staging}
ACCOUNT_ID="835422347653"
REGION="ap-southeast-2"
REPO="bpel2orkes"
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO}"
TAG=$(git rev-parse --short HEAD)

if [[ "$ENV" == "production" ]]; then
  CLUSTER="bpel2orkes-prod"
  SERVICE="bpel2orkes-prod"
  IMAGE_TAG="prod-${TAG}"
  read -p "⚠️  Deploying to PRODUCTION. Are you sure? (yes/no): " CONFIRM
  [[ "$CONFIRM" == "yes" ]] || { echo "Aborted."; exit 1; }
elif [[ "$ENV" == "staging" ]]; then
  CLUSTER="bpel2orkes-staging"
  SERVICE="bpel2orkes-staging"
  IMAGE_TAG="staging-${TAG}"
else
  echo "Usage: $0 [staging|production]"
  exit 1
fi

echo "→ Deploying to ${ENV} (${IMAGE_TAG})"

# 1. Authenticate Docker to ECR
echo "→ Authenticating with ECR..."
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ECR_URI"

# 2. Build image
echo "→ Building Docker image..."
docker build --platform linux/amd64 -t "${REPO}:${IMAGE_TAG}" .

# 3. Tag and push
echo "→ Pushing to ECR..."
docker tag "${REPO}:${IMAGE_TAG}" "${ECR_URI}:${IMAGE_TAG}"
docker tag "${REPO}:${IMAGE_TAG}" "${ECR_URI}:${ENV}-latest"
docker push "${ECR_URI}:${IMAGE_TAG}"
docker push "${ECR_URI}:${ENV}-latest"

# 4. Force ECS to redeploy with new image
echo "→ Triggering ECS redeploy..."
aws ecs update-service \
  --region "$REGION" \
  --cluster "$CLUSTER" \
  --service "$SERVICE" \
  --force-new-deployment \
  --query "service.deployments[0].{status:status,desiredCount:desiredCount}" \
  --output table

echo ""
echo "✓ Deploy triggered for ${ENV}"
echo "  Image: ${ECR_URI}:${IMAGE_TAG}"
echo ""
echo "→ Watch rollout:"
echo "  aws ecs wait services-stable --region ${REGION} --cluster ${CLUSTER} --services ${SERVICE}"
echo ""

# 5. Smoke test (staging only — prod smoke test is manual)
if [[ "$ENV" == "staging" ]]; then
  echo "→ Waiting 30s for service to stabilise..."
  sleep 30
  STAGING_URL="https://staging.bpel2orkes.kshetra.studio"
  echo "→ Smoke test: ${STAGING_URL}/api/v1/health"
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${STAGING_URL}/api/v1/health")
  if [[ "$STATUS" == "200" ]]; then
    echo "✓ Health check passed (HTTP ${STATUS})"
  else
    echo "✗ Health check failed (HTTP ${STATUS}) — check ECS logs"
    exit 1
  fi
fi
