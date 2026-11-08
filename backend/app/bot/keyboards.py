import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Approval, Person
from app.models.base import ApprovalStatus
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

MENU_BUTTONS = ["PERSONS", "GROUPS", "AI ASSISTANT", "CONVERSATION INTELLIGENCE", "APPROVAL CENTER", "ANALYTICS", "SETTINGS"]


def main_menu() -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=t)] for t in MENU_BUTTONS]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def person_actions(person_id: uuid.UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Consent invite", callback_data=f"pp:inv:{person_id}"),
        InlineKeyboardButton(text="Delete", callback_data=f"pp:del:{person_id}"),
    ]])


def confirm_delete(person_id: uuid.UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Yes, delete everything", callback_data=f"pp:delok:{person_id}"),
        InlineKeyboardButton(text="Cancel", callback_data=f"pp:view:{person_id}"),
    ]])


def approval_actions(approval_id: uuid.UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="SEND", callback_data=f"ap:send:{approval_id}"),
        InlineKeyboardButton(text="EDIT", callback_data=f"ap:edit:{approval_id}"),
    ], [
        InlineKeyboardButton(text="REGENERATE", callback_data=f"ap:regen:{approval_id}"),
        InlineKeyboardButton(text="REJECT", callback_data=f"ap:rej:{approval_id}"),
    ]])


def consent_prompt(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="I agree", callback_data=f"cs:yes:{token}"),
        InlineKeyboardButton(text="No thanks", callback_data=f"cs:no:{token}"),
    ]])


def people_list(persons: list[Person]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=p.display_name[:60], callback_data=f"pp:view:{p.id}")] for p in persons])


def approval_text(a: Approval, person_name: str) -> str:
    head = f"Approval {str(a.id)[:8]} · rev {a.revision} · {a.status.value}"
    tail = ""
    if a.status == ApprovalStatus.PENDING:
        tail = f"\nExpires: {a.expires_at:%Y-%m-%d %H:%M} UTC"
    if a.last_error:
        tail += f"\nError: {a.last_error}"
    return f"{head}\nTo: {person_name}{tail}\n\n{a.text}"


async def pending_with_names(session: AsyncSession, owner_id: uuid.UUID) -> list[tuple[Approval, str]]:
    rows = await session.execute(
        select(Approval, Person.display_name).join(Person, Person.id == Approval.person_id)
        .where(Approval.owner_id == owner_id, Approval.status == ApprovalStatus.PENDING)
        .order_by(Approval.created_at))
    return [(a, n) for a, n in rows.all()]
