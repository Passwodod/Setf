import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import CurrentUser, Session
from app.schemas.common import Page
from app.schemas.person import PersonCreate, PersonOut, PersonUpdate
from app.services import persons as svc

router = APIRouter(prefix="/persons", tags=["persons"])


def _404() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "person not found")


@router.get("", response_model=Page[PersonOut])
async def list_persons(
    user: CurrentUser, session: Session,
    q: Annotated[str | None, Query(max_length=100)] = None,
    category: Annotated[str | None, Query(max_length=64)] = None,
    sort: Literal["display_name", "created_at", "updated_at"] = "display_name",
    desc: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[PersonOut]:
    items, total = await svc.list_persons(session, user.id, q=q, category=category, sort=sort,
                                          descending=desc, limit=limit, offset=offset)
    return Page(items=[PersonOut.model_validate(p) for p in items], total=total, limit=limit, offset=offset)


@router.post("", response_model=PersonOut, status_code=status.HTTP_201_CREATED)
async def create_person(body: PersonCreate, user: CurrentUser, session: Session) -> PersonOut:
    try:
        person = await svc.create_person(session, user.id, body, f"user:{user.id}")
    except svc.DuplicateIdentifier:
        raise HTTPException(status.HTTP_409_CONFLICT, "telegram username already linked to a person") from None
    await session.refresh(person)
    return PersonOut.model_validate(person)


@router.get("/{person_id}", response_model=PersonOut)
async def get_person(person_id: uuid.UUID, user: CurrentUser, session: Session) -> PersonOut:
    try:
        return PersonOut.model_validate(await svc.get_person(session, user.id, person_id))
    except svc.PersonNotFound:
        raise _404() from None


@router.patch("/{person_id}", response_model=PersonOut)
async def update_person(person_id: uuid.UUID, body: PersonUpdate, user: CurrentUser, session: Session) -> PersonOut:
    try:
        return PersonOut.model_validate(await svc.update_person(session, user.id, person_id, body, f"user:{user.id}"))
    except svc.PersonNotFound:
        raise _404() from None


@router.delete("/{person_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_person(person_id: uuid.UUID, user: CurrentUser, session: Session) -> Response:
    try:
        await svc.delete_person(session, user.id, person_id, f"user:{user.id}")
    except svc.PersonNotFound:
        raise _404() from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
