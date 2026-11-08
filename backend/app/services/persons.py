import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Embedding, Person, PersonIdentifier
from app.schemas.person import PersonCreate, PersonUpdate
from app.services.audit import audit

SORTABLE = {"display_name": Person.display_name, "created_at": Person.created_at, "updated_at": Person.updated_at}


class PersonNotFound(Exception):
    pass


class DuplicateIdentifier(Exception):
    pass


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def get_person(session: AsyncSession, owner_id: uuid.UUID, person_id: uuid.UUID) -> Person:
    person = await session.scalar(select(Person).where(Person.id == person_id, Person.owner_id == owner_id))
    if person is None:
        raise PersonNotFound
    return person


async def list_persons(
    session: AsyncSession, owner_id: uuid.UUID, *, q: str | None, category: str | None,
    sort: str, descending: bool, limit: int, offset: int,
) -> tuple[list[Person], int]:
    stmt = select(Person).where(Person.owner_id == owner_id)
    if q:
        stmt = stmt.where(Person.display_name.ilike(f"%{_escape_like(q)}%", escape="\\"))
    if category:
        stmt = stmt.where(Person.category == category)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    col = SORTABLE.get(sort, Person.display_name)
    stmt = stmt.order_by(col.desc() if descending else col.asc(), Person.id).limit(limit).offset(offset)
    return list((await session.scalars(stmt)).all()), total


async def create_person(session: AsyncSession, owner_id: uuid.UUID, data: PersonCreate, actor: str) -> Person:
    values = data.model_dump(exclude={"telegram_username"})
    person = Person(owner_id=owner_id, **values)
    session.add(person)
    await session.flush()
    if data.telegram_username:
        username = data.telegram_username.lstrip("@").lower()
        taken = await session.scalar(
            select(PersonIdentifier.id).where(
                PersonIdentifier.owner_id == owner_id,
                PersonIdentifier.kind == "telegram_username",
                PersonIdentifier.value == username,
            )
        )
        if taken:
            raise DuplicateIdentifier
        session.add(PersonIdentifier(owner_id=owner_id, person_id=person.id, kind="telegram_username", value=username))
    await audit(session, owner_id=owner_id, actor=actor, action="person.create", entity_type="person", entity_id=person.id)
    return person


async def update_person(session: AsyncSession, owner_id: uuid.UUID, person_id: uuid.UUID, data: PersonUpdate, actor: str) -> Person:
    person = await get_person(session, owner_id, person_id)
    changes = data.model_dump(exclude_unset=True)
    if "display_name" in changes and changes["display_name"] is None:
        changes.pop("display_name")
    for key, value in changes.items():
        setattr(person, key, value)
    await session.flush()
    await session.refresh(person)
    await audit(session, owner_id=owner_id, actor=actor, action="person.update", entity_type="person",
                entity_id=person.id, details={"fields": sorted(changes)})
    return person


async def delete_person(session: AsyncSession, owner_id: uuid.UUID, person_id: uuid.UUID, actor: str) -> None:
    """Hard delete. FK cascades remove identifiers, interests, observations, evidence, timelines and
    embeddings tied to the person; embeddings of their messages are removed explicitly."""
    person = await get_person(session, owner_id, person_id)
    await session.execute(delete(Embedding).where(Embedding.owner_id == owner_id, Embedding.person_id == person.id))
    await session.delete(person)
    await audit(session, owner_id=owner_id, actor=actor, action="person.delete", entity_type="person", entity_id=person_id)


async def find_persons(session: AsyncSession, owner_id: uuid.UUID, query: str, limit: int = 10) -> list[Person]:
    """Resolve a bot argument: full UUID, or case-insensitive name fragment."""
    query = query.strip()
    try:
        pid = uuid.UUID(query)
        p = await session.scalar(select(Person).where(Person.id == pid, Person.owner_id == owner_id))
        return [p] if p else []
    except ValueError:
        pass
    stmt = (select(Person).where(Person.owner_id == owner_id, Person.display_name.ilike(f"%{_escape_like(query)}%", escape="\\"))
            .order_by(Person.display_name).limit(limit))
    return list((await session.scalars(stmt)).all())
