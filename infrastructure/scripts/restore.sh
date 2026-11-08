#!/usr/bin/env bash
set -euo pipefail
[ $# -eq 1 ] || { echo "usage: $0 <dump file>"; exit 1; }
cd "$(dirname "$0")/../.."
source ./.env
read -r -p "This REPLACES database '$POSTGRES_DB'. Type 'restore' to continue: " ok
[ "$ok" = restore ] || exit 1
docker compose stop backend
docker compose exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner < "$1"
docker compose up -d backend
