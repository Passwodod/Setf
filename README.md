# Social Intelligence AI

Consent-based platform for organising what you know about people who have agreed to take part:
Telegram bot, backend API, AI analysis, and a web dashboard.

## Status (honest checklist)

Legend: ✅ implemented and covered by passing automated tests · ⬜ not started.

| Area | State |
|---|---|
| PostgreSQL schema (all 29 specified tables, pgvector, FTS indexes, integrity constraints) via Alembic | ✅ |
| Auth: register/login/refresh/logout, argon2id, JWT with revocation, rate limiting, audit log | ✅ |
| Persons CRUD with owner isolation, search/filter/sort/pagination | ✅ |
| Health checks, Dockerfile, docker-compose, Caddy, backup/restore scripts, CI | written, **not executed** (no Docker in the dev sandbox) |
| Telegram bot (aiogram, webhook, menu, commands) | ⬜ Phase 2 |
| Group collection, consent flows, retention, deletion/export | ⬜ Phase 3 |
| AI engine (extraction, interests, topics, RAG, hybrid search) | ⬜ Phase 4 |
| Web dashboard (Next.js) | ⬜ Phase 5 |
| Conversation intelligence | ⬜ Phase 6 |
| Interaction agent, approval center, interviews | ⬜ Phase 7 |
| Social graph | ⬜ Phase 8 |
| Background workers, monitoring, hardening | ⬜ Phase 9 |

The acceptance criteria are **not** met yet; only the Phase 1 foundation is done.

## Development

```bash
cd backend
cp ../.env.example .env   # set SIA_JWT_SECRET (>= 32 chars), SIA_DATABASE_URL, SIA_REDIS_URL
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
uv run pytest            # needs PostgreSQL 16 + pgvector (db sia_test) and Redis
```

API docs: `/docs` (non-production) and `/api/v1/openapi.json`.

## Deployment

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
