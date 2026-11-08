"""Consent flow for the people being tracked. Open to everyone; it only ever acts on a signed token."""
from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select, update

from app.bot.keyboards import consent_prompt
from app.models import Person, PersonIdentifier
from app.models.base import ConsentScope, ConsentStatus
from app.services import consent as consent_svc
from app.services.audit import audit
from app.services.consent_invite import parse_token

router = Router(name="consent")

NOTICE = (
    "Hello! I'm an automated assistant (a bot, not a human).\n\n"
    "The owner of this bot would like to keep notes about your interests and what you voluntarily tell me, "
    "and to be able to send you messages through me (each message is approved by a human before sending).\n\n"
    "If you agree: what you write to me is stored, you can ask me to stop at any time with /revoke, and "
    "you can ask for your data to be deleted.\n\nDo you agree?"
)


@router.message(CommandStart(deep_link=True))
async def start_with_payload(message: Message, command: CommandObject, session, user=None):
    arg = command.args or ""
    if not arg.startswith("c_"):
        if user is None:
            await message.answer("This bot is private.")
            return
        from app.bot.handlers.admin import start_admin
        await start_admin(message, user)
        return
    pid = parse_token(arg[2:])
    person = await session.get(Person, pid) if pid else None
    if person is None:
        await message.answer("This invitation link is not valid.")
        return
    await message.answer(NOTICE, reply_markup=consent_prompt(arg[2:]))


@router.callback_query(F.data.startswith("cs:"))
async def consent_choice(cb: CallbackQuery, session):
    _, choice, token = (cb.data.split(":", 2) + ["", ""])[:3]
    pid = parse_token(token)
    person = await session.get(Person, pid) if pid else None
    if person is None or cb.message is None:
        await cb.answer("Invalid invitation", show_alert=True)
        return
    scopes = (ConsentScope.PROFILING, ConsentScope.DIRECT_MESSAGING)
    if choice == "yes":
        for sc in scopes:
            await consent_svc.record_consent(session, person.owner_id, sc, ConsentStatus.GRANTED, "bot_button",
                                             person_id=person.id, notice_text=NOTICE)
        person.bot_chat_id = cb.from_user.id
        exists = await session.scalar(select(PersonIdentifier.id).where(
            PersonIdentifier.owner_id == person.owner_id, PersonIdentifier.kind == "telegram_id",
            PersonIdentifier.value == str(cb.from_user.id)))
        if not exists:
            session.add(PersonIdentifier(owner_id=person.owner_id, person_id=person.id, kind="telegram_id",
                                         value=str(cb.from_user.id)))
        await audit(session, owner_id=person.owner_id, actor="bot", action="consent.granted", entity_type="person", entity_id=person.id)
        await cb.message.edit_text("Thank you! Consent recorded. Send /revoke any time to withdraw it.")
    else:
        for sc in scopes:
            await consent_svc.record_consent(session, person.owner_id, sc, ConsentStatus.REVOKED, "bot_button", person_id=person.id)
        await audit(session, owner_id=person.owner_id, actor="bot", action="consent.declined", entity_type="person", entity_id=person.id)
        await cb.message.edit_text("No problem. Nothing will be stored and you won't receive messages.")
    await cb.answer()


@router.message(Command("revoke"))
async def revoke(message: Message, session):
    people = list((await session.scalars(select(Person).where(Person.bot_chat_id == message.chat.id))).all())
    if not people:
        await message.answer("There is no active consent linked to this chat.")
        return
    for p in people:
        for sc in (ConsentScope.PROFILING, ConsentScope.DIRECT_MESSAGING):
            await consent_svc.record_consent(session, p.owner_id, sc, ConsentStatus.REVOKED, "bot_command", person_id=p.id)
        await session.execute(update(Person).where(Person.id == p.id).values(bot_chat_id=None))
        await audit(session, owner_id=p.owner_id, actor="bot", action="consent.revoked", entity_type="person", entity_id=p.id)
    await message.answer("Consent withdrawn. I will not store anything further or message you. "
                         "To have already stored data deleted, ask the owner or reply 'delete my data' to them.")
