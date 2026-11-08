# Architecture

- **backend/** FastAPI app. `api/` is thin HTTP; business logic lives in `services/` so the Telegram bot and
  dashboard share it through the same API/services (no duplicated rules). `models/` SQLAlchemy 2.0,
  `migrations/` Alembic (the only way the schema changes).
- **Tenancy:** every row carries `owner_id`; every query filters on it. Foreign-owner IDs return 404.
- **Provenance:** analytic claims are `observations` with `status` (VERIFIED, SELF_REPORTED, OBSERVED,
  INFERRED, UNKNOWN, CONTRADICTED), `confidence` (low/medium/high, deliberately not numeric), source type,
  and `evidence` rows. History is kept via `supersedes_id`; conflicts via `contradicts_id`.
  DB constraints stop AI-sourced rows from being VERIFIED.
- **Consent:** `consent_records` is an append-only ledger; the latest row per subject/scope is current.
- **Idempotency:** unique `(group_id, telegram_message_id)` for messages and `(owner_id, idempotency_key)` for observations.
- **Planned:** bot (aiogram webhook) and worker (Redis queue) as separate containers using the same services.

# Database

See `backend/app/models/` (29 tables). Notable: generated `tsvector` columns + GIN for full-text search,
`embeddings.vector` `vector(1536)` with HNSW cosine index, `persons`→children `ON DELETE CASCADE` so
deleting a person removes derived data.

# Security (current)

argon2id passwords; JWT access/refresh with per-user `token_version` (logout revokes all tokens);
Redis rate limiting on login/register (fails closed); uniform login errors; strict pydantic schemas
(`extra=forbid`, no `owner_id` accepted); parametrised queries with escaped LIKE; security headers;
audit log. Secrets only via environment (`SIA_JWT_SECRET` is required and validated at start).
