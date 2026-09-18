#!/usr/bin/env bash
# ============================================
# Deploy LearnLeader image to the USA VPS.
#
# The VPS runs docker-compose.deploy.yml (kept outside git, in ~/deeptutor)
# which pulls ghcr.io/hubo1989/learnleader:${IMAGE_TAG:-latest}.
#
# Usage:
#   scripts/deploy_usa.sh              # deploy :latest
#   scripts/deploy_usa.sh 1.6.2        # deploy a specific version tag
#   scripts/deploy_usa.sh --rollback 1.6.1   # same as passing a version; kept for clarity
#   scripts/deploy_usa.sh --status     # show running image + health
#
# Prerequisites:
#   - `usa` host configured in ~/.ssh/config
#   - VPS docker logged in to ghcr.io with a read:packages PAT:
#       ssh usa 'docker login ghcr.io -u hubo1989 -p <READ_PAT>'
# ============================================
set -euo pipefail

HOST="usa"
COMPOSE_FILE="docker-compose.deploy.yml"
CONTAINER="deeptutor"
HEALTH_TIMEOUT=180

tag="latest"
case "${1:-}" in
"")
    tag="latest"
    ;;
--rollback)
    [ -n "${2:-}" ] || { echo "usage: $0 --rollback <version>" >&2; exit 1; }
    tag="$2"
    ;;
--status)
    exec ssh -o BatchMode=yes "$HOST" \
        "docker ps --filter name=${CONTAINER} --format '{{.Image}}  {{.Status}}'"
    ;;
-h|--help)
    sed -n '2,20p' "$0"; exit 0
    ;;
*)
    tag="$1"
    ;;
esac

echo "==> Deploying ghcr.io/hubo1989/learnleader:${tag} to ${HOST}"

ssh -o BatchMode=yes "$HOST" "set -e
cd ~/deeptutor
IMAGE_TAG=${tag} docker compose -f ${COMPOSE_FILE} pull 2>&1 | tail -2
IMAGE_TAG=${tag} docker compose -f ${COMPOSE_FILE} up -d 2>&1 | tail -2"

echo "==> Waiting for health check (max ${HEALTH_TIMEOUT}s)..."
deadline=$((SECONDS + HEALTH_TIMEOUT))
while [ $SECONDS -lt $deadline ]; do
    status=$(ssh -o BatchMode=yes "$HOST" \
        "docker inspect ${CONTAINER} --format '{{.State.Health.Status}}' 2>/dev/null" || true)
    case "$status" in
        healthy)
            ssh -o BatchMode=yes "$HOST" \
                "docker ps --filter name=${CONTAINER} --format '==> {{.Image}}  {{.Status}}'"
            echo "==> Deploy OK"
            exit 0
            ;;
        unhealthy)
            echo "!! Container reported unhealthy. Recent logs:" >&2
            ssh -o BatchMode=yes "$HOST" "docker logs --tail 30 ${CONTAINER}" >&2
            exit 1
            ;;
    esac
    sleep 10
done

echo "!! Timed out waiting for healthy status." >&2
ssh -o BatchMode=yes "$HOST" "docker logs --tail 30 ${CONTAINER}" >&2
exit 1
