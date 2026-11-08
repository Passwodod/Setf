import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db import get_engine

EXPECTED = {
    "users", "persons", "person_identifiers", "groups", "group_members", "messages", "message_sources",
    "interests", "person_interests", "professional_information", "projects", "observations", "evidence",
    "timelines", "conversations", "conversation_messages", "interviews", "interview_answers",
    "topic_categories", "topic_mentions", "social_connections", "interaction_events", "ai_insights",
    "embeddings", "approvals", "consent_records", "data_requests", "audit_logs", "system_settings",
}


async def test_all_specified_tables_exist():
    async with get_engine().connect() as conn:
        rows = await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
        assert EXPECTED <= {r[0] for r in rows}
        ext = {r[0] for r in await conn.execute(text("SELECT extname FROM pg_extension"))}
    assert "vector" in ext


async def _seed(conn):
    uid, pid = uuid.uuid4(), uuid.uuid4()
    await conn.execute(text("INSERT INTO users(id,email,password_hash,display_name,role) VALUES (:i,'u@x.io','h','', 'owner')"), {"i": uid})
    await conn.execute(text("INSERT INTO persons(id,owner_id,display_name) VALUES (:p,:o,'P')"), {"p": pid, "o": uid})
    return uid, pid


async def test_ai_cannot_record_verified_fact():
    with pytest.raises(IntegrityError):
        async with get_engine().begin() as conn:
            uid, pid = await _seed(conn)
            await conn.execute(text(
                "INSERT INTO observations(id,owner_id,person_id,category,claim,source_type,status,confidence) "
                "VALUES (gen_random_uuid(),:o,:p,'interest','likes AI','ai_inference','VERIFIED','low')"), {"o": uid, "p": pid})


async def test_cascade_delete_person_removes_observations():
    async with get_engine().begin() as conn:
        uid, pid = await _seed(conn)
        await conn.execute(text(
            "INSERT INTO observations(id,owner_id,person_id,category,claim,source_type,status,confidence) "
            "VALUES (gen_random_uuid(),:o,:p,'interest','likes AI','self_report','SELF_REPORTED','medium')"), {"o": uid, "p": pid})
        await conn.execute(text("DELETE FROM persons WHERE id=:p"), {"p": pid})
        assert (await conn.execute(text("SELECT count(*) FROM observations"))).scalar() == 0


async def test_message_redelivery_is_idempotent_by_constraint():
    with pytest.raises(IntegrityError):
        async with get_engine().begin() as conn:
            uid, _ = await _seed(conn)
            gid = uuid.uuid4()
            await conn.execute(text("INSERT INTO groups(id,owner_id,telegram_chat_id,title) VALUES (:g,:o,-100,'G')"), {"g": gid, "o": uid})
            for _ in range(2):
                await conn.execute(text("INSERT INTO messages(id,owner_id,group_id,telegram_message_id,text,sent_at) VALUES (gen_random_uuid(),:o,:g,5,'hi',now())"), {"o": uid, "g": gid})


async def test_vector_column_roundtrip():
    async with get_engine().begin() as conn:
        uid, pid = await _seed(conn)
        vec = "[" + ",".join(["0.1"] * 1536) + "]"
        await conn.execute(text("INSERT INTO embeddings(id,owner_id,person_id,model_name,content_hash,vector) VALUES (gen_random_uuid(),:o,:p,'m','h',CAST(:v AS vector))"), {"o": uid, "p": pid, "v": vec})
        d = (await conn.execute(text("SELECT vector <=> CAST(:v AS vector) FROM embeddings"), {"v": vec})).scalar()
    assert d == pytest.approx(0, abs=1e-6)


async def test_health_endpoints(client):
    assert (await client.get("/healthz")).status_code == 200
    assert (await client.get("/readyz")).status_code == 200
