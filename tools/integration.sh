#!/bin/sh
# Destructive cleanup is confined to a unique, generated CI instance.
set -eu
if [ "$(printenv HARBOR_RUN_INTEGRATION || true)" != 1 ]; then echo 'Run only on an isolated Docker staging/CI host; set HARBOR_RUN_INTEGRATION=1' >&2; exit 1; fi
project="harbor-ci-$(date +%s)-$$"
case "$project" in harbor-ci-[0-9]*-[0-9]*) ;; *) exit 1;; esac
envfile=$(mktemp)
cat > "$envfile" <<EOF
HARBOR_INSTANCE_ID=$project
HARBOR_HOSTNAME=registry.company.test
HARBOR_ADMIN_PASSWORD=test-admin-password-with-entropy
HARBOR_DB_PASSWORD=test-database-password-with-entropy
BACKUP_ENABLED=false
EOF
compose() { docker compose --env-file "$envfile" -p "$project" -f compose.yaml "$@"; }
cleanup() {
  compose logs --no-color --tail=60 || true
  # Both the Compose project and explicit named volumes use the generated CI ID.
  compose down --volumes --remove-orphans || true
  rm -f "$envfile"
}
trap cleanup EXIT HUP INT TERM
docker network inspect dokploy-network >/dev/null 2>&1 || docker network create dokploy-network
compose config --quiet
compose up -d --build --wait --wait-timeout 600
compose exec -T proxy curl --fail --silent http://localhost:8080/api/v2.0/health
challenge=$(compose exec -T proxy curl --silent -I http://localhost:8080/v2/)
printf '%s' "$challenge" | python3 -c 'import sys; value=sys.stdin.read().lower(); assert "401" in value and "https://registry.company.test/service/token" in value'
# No Docker socket exists in resident operations; disabled backup must still succeed.
compose exec -T operations python3 /app/operations.py backup
compose exec -T operations python3 /app/operations.py check
before=$(compose exec -T core sha256sum /etc/core/key /etc/core/private_key.pem)
compose restart
compose up -d --wait --wait-timeout 600
after=$(compose exec -T core sha256sum /etc/core/key /etc/core/private_key.pem)
[ "$before" = "$after" ]
compose up -d --force-recreate --wait --wait-timeout 600
after=$(compose exec -T core sha256sum /etc/core/key /etc/core/private_key.pem)
[ "$before" = "$after" ]
echo 'Docker prepare, health, token URL, disabled backup, restart and redeploy smoke tests passed'
