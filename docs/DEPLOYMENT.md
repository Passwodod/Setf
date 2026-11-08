# Deployment (Ubuntu 24.04 + Docker Compose)

Status: files written, **not yet executed** — verify on the target host.

1. Inspect the host first: `ss -tlnp`, `docker ps` — Caddy needs 80/443; nothing else is published.
2. `cp .env.example .env`, fill in strong values (`openssl rand -hex 32` for `SIA_JWT_SECRET`).
3. `docker compose up -d --build` — `migrate` runs Alembic before `backend` starts.
4. Create the first account (becomes admin): `POST /api/v1/auth/register`; registration then closes unless `SIA_ALLOW_REGISTRATION=true`.
5. Update: `git pull && docker compose up -d --build` (named volumes keep data).
6. Backups: `infrastructure/scripts/backup.sh` (cron it); restore: `infrastructure/scripts/restore.sh <dump>`.

PostgreSQL and Redis have no published ports.
