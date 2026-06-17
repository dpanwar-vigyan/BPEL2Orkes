#!/bin/bash
# Deploy script — builds ARM64 Lambda image, pushes to ECR, updates Lambda function
# Usage:
#   ./scripts/deploy.sh staging
#   ./scripts/deploy.sh production

set -euo pipefail

ENV=${1:-staging}
ACCOUNT_ID="835422347653"
REGION="ap-southeast-2"
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/bpel2orkes"
TAG=$(git rev-parse --short HEAD)
IMAGE_TAG="${ENV}-${TAG}"
FUNCTION="bpel2orkes-${ENV}"

if [[ "$ENV" == "production" ]]; then
  read -p "⚠️  Deploying to PRODUCTION. Are you sure? (yes/no): " CONFIRM
  [[ "$CONFIRM" == "yes" ]] || { echo "Aborted."; exit 1; }
elif [[ "$ENV" != "staging" ]]; then
  echo "Usage: $0 [staging|production]"
  exit 1
fi

echo "→ Deploying to ${ENV} (${IMAGE_TAG})"

# 1. Authenticate Docker to ECR
echo "→ Authenticating with ECR..."
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ECR_URI"

# 2. Build + push (ARM64, single-arch manifest so Lambda accepts it)
echo "→ Building and pushing Docker image..."
docker buildx build \
  --platform linux/arm64 \
  -f Dockerfile.lambda \
  --provenance=false \
  --sbom=false \
  -t "${ECR_URI}:${IMAGE_TAG}" \
  -t "${ECR_URI}:${ENV}-latest" \
  --push \
  .

# 3. Update Lambda function code
echo "→ Updating Lambda function..."
aws lambda update-function-code \
  --region "$REGION" \
  --function-name "$FUNCTION" \
  --image-uri "${ECR_URI}:${IMAGE_TAG}" \
  --query "{Status:LastUpdateStatus,State:State}" \
  --output table

# 4. Wait for update to complete
echo "→ Waiting for Lambda to stabilise..."
aws lambda wait function-updated --region "$REGION" --function-name "$FUNCTION"

# 5. Smoke test
URL="https://$([ "$ENV" == "production" ] && echo "bpel2orkes.kshetra.studio" || echo "staging.bpel2orkes.kshetra.studio")"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${URL}/api/v1/health")
if [[ "$STATUS" == "200" ]]; then
  echo "✓ Health check passed — ${URL}"
else
  echo "✗ Health check failed (HTTP ${STATUS}) — check Lambda logs:"
  echo "  aws logs tail /aws/lambda/${FUNCTION} --region ${REGION} --since 5m"
  exit 1
fi

echo ""
echo "✓ Deploy complete: ${ECR_URI}:${IMAGE_TAG}"
