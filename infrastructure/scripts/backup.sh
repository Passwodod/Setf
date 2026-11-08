#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
source ./.env
out="infrastructure/backups/sia-$(date -u +%Y%m%dT%H%M%SZ).dump"
umask 077
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$out"
# keep the 14 most recent backups
ls -1t infrastructure/backups/sia-*.dump | tail -n +15 | while read -r old; do rm -- "$old"; done
echo "backup written: $out"
