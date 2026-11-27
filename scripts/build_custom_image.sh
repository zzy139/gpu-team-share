#!/bin/bash -e
# Build the customized PCUI Lambda image (linux/amd64) and push it to a private ECR repository.
# Prints only the image URI on stdout; progress goes to stderr.
#
# Usage: build_custom_image.sh --region REGION --tag TAG [--repo REPO]
REPO=pcui-custom
USAGE="$(basename "$0") [-h] --region REGION --tag TAG [--repo REPO]"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h) echo "$USAGE" >&2; exit 1 ;;
    --region) REGION="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    *) echo "$USAGE" >&2; exit 1 ;;
  esac
done

if [ -z "$REGION" ] || [ -z "$TAG" ]; then
  echo "$USAGE" >&2
  exit 1
fi
if ! [[ "$TAG" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]] || ! [[ "$REPO" =~ ^[a-z0-9][a-z0-9._/-]{1,255}$ ]]; then
  echo "Error: invalid --tag or --repo value" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_ENDPOINT="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE="${ECR_ENDPOINT}/${REPO}:${TAG}"

{
  echo "Logging in to ${ECR_ENDPOINT}..."
  aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ECR_ENDPOINT"

  echo "Building frontend image..."
  docker build --platform linux/amd64 --provenance false --build-arg PUBLIC_URL=/ -t frontend-awslambda "$ROOT/frontend"

  echo "Building Lambda image ${IMAGE}..."
  docker build --platform linux/amd64 --provenance false -f "$ROOT/Dockerfile.awslambda" -t "$IMAGE" "$ROOT"

  echo "Pushing ${IMAGE}..."
  docker push "$IMAGE"
} >&2

echo "$IMAGE"
