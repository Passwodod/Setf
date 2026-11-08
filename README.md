# SOCIAL INTELLIGENCE AI (SIA)

A **private, consent-based** platform for organising what you know about the people around you: their
interests, work, projects and how to talk with them. It has a Telegram bot, a backend API, an AI analysis
engine and (planned) a web dashboard.

> **Status: work in progress.** The backend foundation, Telegram bot, consent/approval flows, job queue and
> group collection are built and tested. The AI engine is partly built and **untested end to end**. The web
> dashboard does not exist yet. See [Status](#status-what-is-actually-finished) and [Plan](#plan).

## What kind of system is this?

**Collect (with permission) → structure → analyse → explain → suggest.**

| Layer | What it does | Tech |
|---|---|---|
| Telegram bot | Admin menu, person management, consent invitations, **approval center** for outgoing messages, group collection | aiogram 3, webhook |
| Telegram account reader (MTProto) | Read-only access to *your own* account for sources you pick explicitly | Telethon |
| Backend API | Auth, persons, groups, approvals, jobs, privacy, Telegram accounts. All business logic lives in `services/` so bot and API share it | FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic |
| Workers | Long-running imports and retention purges run outside HTTP/bot handlers | Redis queue, own worker process |
| AI engine | Language-aware extraction, validation of model output, provider-agnostic | OpenAI provider, rule-based mock provider |
| Data | 35 tables, full-text search, pgvector embeddings | PostgreSQL 16 + pgvector, Redis |

### Rules the system enforces (in code and tests, not just docs)

- **Nothing outgoing is sent without admin approval** (SEND/EDIT/REJECT). Delivery is exactly-once even under
  double clicks and concurrent attempts, approvals expire after 24 h, and consent is re-checked at send time.
- **Only the Telegram Bot API sends messages.** The MTProto reader has no send/edit/delete functions.
- **Adding a person is not consent.** Private chats need a recorded, granted consent; revocation stops collection.
- **Groups:** a notice is posted, collection is OFF until explicitly enabled, members can `/optout` (their stored
  messages are deleted), and the owner can stop, view, purge or delete everything.
- **No hidden collection:** no scraping, no bypassing Telegram privacy settings, bot-only group collection.
- **Provenance:** every analytic claim carries source, status (VERIFIED / SELF_REPORTED / OBSERVED / INFERRED /
  UNKNOWN / CONTRADICTED), confidence and evidence. The database forbids AI output from being marked VERIFIED.
- **No sensitive inference:** health, religion, politics, orientation, income/wealth etc. are filtered out of AI output.
- **Tenant isolation:** every row has an `owner_id`; other users' IDs return 404.
- **Secrets** only via environment variables; Telegram sessions are stored as Fernet ciphertext.

## Status: what is actually finished

Legend: ✅ implemented and covered by passing automated tests · 🟡 partly built / not verified · ⬜ not started.
Current test suite: **126 passing** (PostgreSQL 16 + pgvector + Redis, migrations applied from scratch in every run).

| Area | State | Notes |
|---|---|---|
| Database schema, Alembic migrations (0001–0004) | ✅ | Constraints enforce idempotency, cascade deletes, AI-not-verified. `alembic check` clean |
| Auth & security | ✅ | argon2id, JWT access/refresh with revocation, Redis rate limits, audit log, strict schemas, security headers. Tests: forged/expired/`none` tokens, cross-user access, SQLi, XSS, mass assignment |
| Persons API | ✅ | CRUD, search, filter, sort, pagination |
| Telegram bot (Phase 2) | ✅ against a **simulated** Bot API | Secret-verified webhook + update dedup, admin-only gate, `/add_person /people /profile /note /invite /delete /approvals /settings`, consent flow + `/revoke` |
| Approval center | ✅ (bot + API) | Exactly-once send, expiry, edit, reject, cancel, failure handling. REGENERATE needs the AI engine ⬜ |
| MTProto accounts (§29) | ✅ backend, simulated gateway | Connect + 2FA + re-auth, multiple accounts, encrypted sessions, explicit sources, consent gating, dedup import, disconnect + erase. **Never run against live Telegram** |
| Multilingual layer (§28) | ✅ rule-based part | Detection (en/ru/kk/ky/tr/de/fr/es/ar/zh + translit), slang/typo lexicon, ambiguity handling, cross-language concepts & message search. Hand-written alias table; not a full NLP system |
| Background jobs | ✅ | Redis queue, retries with exponential backoff + jitter, dead-letter queue, idempotency keys, crash recovery, concurrency limit, jobs API. Telegram import has an opt-in background endpoint; the original synchronous `/import` is kept for compatibility |
| Retention purge | ✅ | Hourly job deletes expired messages and evidence excerpts |
| Group collection via bot | ✅ simulated Bot API | Notice, explicit enable, privacy-mode warning, opt-out/opt-in, stop, purge, groups API |
| AI provider architecture | 🟡 | Interface, strict schemas, prompts, **mock provider**, **OpenAI provider**, output validators (verbatim-quote check, sensitive filter, confidence cap) are written and import cleanly, but have **no tests yet** and OpenAI has **never been called** |
| AI extraction pipeline → observations/evidence/timeline | ⬜ | Next |
| Structured intelligence profiles, contradiction detection | ⬜ | |
| Conversation intelligence (topics, starters, questions, common interests) | ⬜ | Only the schema/validator pieces exist |
| Hybrid search (FTS + pgvector), AI assistant, interviews | ⬜ | Cross-language keyword search exists; embeddings not wired |
| Web dashboard (Next.js, light/dark, Telegram connections pages) | ⬜ | No frontend code yet |
| Social graph, timeline UI, analytics charts | ⬜ | Tables exist, no logic |
| Docker Compose deployment | 🟡 | Files written (Dockerfile, compose, Caddy, backup/restore, CI). **Never executed** — no Docker in the dev sandbox. Needs a worker service entry too |
| Live Telegram integration test | ⬜ | Needs your own bot token / api_id / api_hash in `.env` and a controlled test group |
| SpiderFoot / OpenOSINT readiness | ⬜ | Architecture only, see Plan |
| Docs: API.md, DATABASE.md, SECURITY.md | ⬜ | Only ARCHITECTURE.md and DEPLOYMENT.md exist |

## Plan

Order is by dependency and risk. After each stage: run the full suite, add tests, check migrations, review security, update this README.

1. **AI engine (in progress):** tests for providers/validators → extraction pipeline as a background job
   (messages → validated claims → observations + evidence + timeline, idempotent, versioned, contradictions kept side by side)
   → structured profile builder → embeddings + hybrid search → AI assistant answering only from stored data, in the user's language.
   A real-provider test is opt-in (`SIA_AI_PROVIDER=openai` + key); mock output is never presented as real AI.
2. **Conversation intelligence:** topic ranking (frequency, recency, confirmed interest; popularity ≠ personal interest),
   common interests, starters/questions in the person's language, avoid-topics respected, REGENERATE in the approval center.
3. **Docker:** add worker + bot services, run the full compose stack on a Docker-capable runtime, health checks, backup/restore drill.
4. **Live Telegram testing:** controlled bot + test group + own account; verify webhook, privacy mode, MTProto login/import.
5. **Dashboard:** Next.js + Tailwind + shadcn/ui + React Query + Recharts; Overview, People, Profile tabs, Groups, Intelligence,
   Approval Center, Assistant, Settings, **Telegram Connections** (8 pages), light/dark theme.
6. **Social graph & timeline:** edges only from evidence (shared membership is a weak edge, never "friendship").
7. **External enrichment (SpiderFoot / OpenOSINT), design only for now:** an `EnrichmentProvider` interface running as an
   isolated worker, restricted to people with recorded consent, admin-approved per run, results stored as
   `INFERRED`/unverified with source URLs, never merged into a profile automatically, full audit trail. Nothing is built.
8. **Hardening:** security audit, load test, monitoring, restore drill, remaining docs.

The acceptance checklist from the original spec is **not met yet**.

## Getting started (development)

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), PostgreSQL 16 with `pgvector` and `pg_trgm`, Redis.

```bash
cd backend
cp ../.env.example .env        # fill in SIA_JWT_SECRET (>= 32 chars) etc.
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload          # API  (docs at /docs when SIA_ENV != production)
uv run python -m app.worker                   # background worker
uv run pytest                                 # needs databases `sia` and `sia_test`, Redis on localhost
```

### Configuration (environment, never commit `.env`)

| Variable | Purpose |
|---|---|
| `SIA_JWT_SECRET` | required, ≥ 32 chars |
| `SIA_DATABASE_URL`, `SIA_REDIS_URL` | connections |
| `SIA_SESSION_ENCRYPTION_KEYS` | Fernet key(s) for MTProto sessions (comma-separated for rotation) |
| `SIA_TELEGRAM_BOT_TOKEN`, `SIA_TELEGRAM_WEBHOOK_SECRET`, `SIA_PUBLIC_URL` | bot + webhook (HTTPS URL required) |
| `SIA_ADMIN_TELEGRAM_IDS` | numeric Telegram IDs allowed to administer the bot |
| `SIA_AI_PROVIDER` (`openai` / `mock`), `SIA_OPENAI_API_KEY`, `SIA_AI_MODEL` | AI provider; `mock` is refused in production |
| `SIA_ALLOW_REGISTRATION` | closed by default after the first (admin) account |

## Repository layout

```
backend/app/
  api/v1/        thin HTTP routers          bot/        aiogram handlers, keyboards (no business logic)
  services/      business logic (shared)    jobs/       Redis queue, handlers
  models/        SQLAlchemy models          ai/         schemas, providers, validators, language, concepts
  security/      passwords, tokens, crypto  worker.py   background worker entrypoint
backend/migrations/   Alembic        backend/tests/   pytest suites
infrastructure/       Caddy, backup/restore scripts   docs/   architecture & deployment notes
```

## Responsible use

For personal use with people who have agreed to take part. Telegram's API terms apply (own `api_id`/`api_hash`,
no bulk automation). Local privacy laws (e.g. GDPR) may apply to stored personal data; export, deletion and
retention controls are part of the design.
