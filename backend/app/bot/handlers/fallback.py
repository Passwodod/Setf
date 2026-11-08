"""Catch-all private text: admin edit mode, or inbound replies from consenting people."""
from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy import select

from app.bot.handlers.approvals import capture_edit
from app.models import Conversation, ConversationMessage, InteractionEvent, Person
from app.models.base import ConsentScope, ConsentStatus
from app.services import consent as consent_svc

router = Router(name="fallback")


@router.message(F.chat.type == "private", F.text)
async def private_text(message: Message, session, user=None):
    if user is not None and await capture_edit(message, session, user):
        return
    people = list((await session.scalars(select(Person).where(Person.bot_chat_id == message.chat.id))).all())
    stored = False
    for p in people:
        if await consent_svc.current_consent(session, p.owner_id, ConsentScope.PROFILING, person_id=p.id) != ConsentStatus.GRANTED:
            continue
        conv = await session.scalar(select(Conversation).where(
            Conversation.owner_id == p.owner_id, Conversation.person_id == p.id, Conversation.status == "open"))
        if conv is None:
            conv = Conversation(owner_id=p.owner_id, person_id=p.id)
            session.add(conv)
            await session.flush()
        session.add(ConversationMessage(owner_id=p.owner_id, conversation_id=conv.id, direction="inbound",
                                        text=message.text[:4096], telegram_message_id=message.message_id))
        session.add(InteractionEvent(owner_id=p.owner_id, person_id=p.id, kind="message_received"))
        stored = True
    if stored:
        await message.answer("Noted. (I'm an automated assistant. /revoke stops storage.)")
    elif user is None:
        await message.answer("This bot is private. If you were invited, use the link you received.")
