#!/bin/sh
# Paste into a Dokploy Server Job on the Harbor Docker host.
# For a local Dokploy Server Job, this also works inside Dokploy's container.
set -eu
HARBOR_INSTANCE_ID="harbor-prod"
ACTION="backup"
# For restore only, add: --snapshot <hex-id> --confirm-target <new-instance-id>
case "$HARBOR_INSTANCE_ID" in *[!a-z0-9-]*|'') echo 'Invalid instance ID' >&2; exit 1;; esac
ids=$(docker ps -q --filter "label=io.harbor-dokploy.instance=$HARBOR_INSTANCE_ID" --filter 'label=io.harbor-dokploy.role=operations')
set -- $ids
if [ "$#" -ne 1 ]; then echo 'Expected exactly one running operations container' >&2; exit 1; fi
anchor=$1
case "$ACTION" in
  backup)
    enabled=$(docker exec "$anchor" python3 /app/operations.py enabled)
    if [ "$enabled" = false ]; then echo 'backup: disabled/skipped'; exit 0; fi
    if [ "$enabled" != true ]; then echo 'Backup configuration unavailable' >&2; exit 1; fi
    ;;
  maintenance|recover|restore) ;;
  *) echo 'Use a Compose Job for policies, gc, monitor, check and repository commands' >&2; exit 1;;
esac
image=$(docker inspect --format '{{.Image}}' "$anchor")
# No socket is mounted into a resident Harbor or operations container.
# The short-lived helper receives host-level Docker privileges for this operation.
docker run --rm --memory=1g --cpus=1 --cap-drop=ALL \
  --cap-add=CHOWN --cap-add=DAC_OVERRIDE --cap-add=FOWNER \
  --security-opt=no-new-privileges:true \
  --volumes-from "$anchor" \
  --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock \
  "$image" "$ACTION"
