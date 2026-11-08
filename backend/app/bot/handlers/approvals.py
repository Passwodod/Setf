import uuid

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as kb
from app.bot.factory import IsAdmin
from app.core.redis import get_redis
from app.models import Person
from app.models.base import ApprovalStatus
from app.services import approvals as svc
from app.services.telegram_send import AiogramSender

router = Router(name="approvals")
router.message.filter(IsAdmin(), F.chat.type == "private")
router.callback_query.filter(IsAdmin())

EDIT_KEY = "bot:edit:{}"


async def show_card(target: Message, session, a, edit: bool = False) -> None:
    person = await session.get(Person, a.person_id)
    text = kb.approval_text(a, person.display_name if person else "?")
    markup = kb.approval_actions(a.id) if a.status == ApprovalStatus.PENDING else None
    if edit:
        await target.edit_text(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


async def notify_admin(bot, session, approval, chat_id: int) -> None:
    """Push a new pending approval to the admin's chat."""
    person = await session.get(Person, approval.person_id)
    msg = await bot.send_message(chat_id, kb.approval_text(approval, person.display_name),
                                 reply_markup=kb.approval_actions(approval.id))
    approval.admin_chat_id, approval.admin_message_id = chat_id, msg.message_id


@router.message(Command("approvals"))
@router.message(F.text == "APPROVAL CENTER")
async def list_pending(message: Message, session, user):
    await svc.expire_stale(session, user.id)
    pending = await kb.pending_with_names(session, user.id)
    if not pending:
        await message.answer("Nothing is waiting for approval.")
        return
    for a, _name in pending:
        await show_card(message, session, a)


@router.message(Command("cancel"))
async def cancel_edit(message: Message):
    await get_redis().delete(EDIT_KEY.format(message.from_user.id))
    await message.answer("Edit cancelled.")


@router.callback_query(F.data.startswith("ap:"))
async def approval_callback(cb: CallbackQuery, session, user, bot):
    parts = cb.data.split(":")
    try:
        action, aid = parts[1], uuid.UUID(parts[2])
    except (IndexError, ValueError):
        await cb.answer("Invalid action", show_alert=True)
        return
    actor = f"bot:{cb.from_user.id}"
    try:
        if action == "send":
            a = await svc.approve_and_send(session, AiogramSender(bot), user.id, aid, actor)
            note = "Sent." if a.status == ApprovalStatus.SENT else f"Not sent: {a.last_error}"
            await cb.answer(note, show_alert=a.status != ApprovalStatus.SENT)
        elif action == "rej":
            a = await svc.reject(session, user.id, aid, actor)
            await cb.answer("Rejected.")
        elif action == "edit":
            a = await svc.get_approval(session, user.id, aid)
            if a.status != ApprovalStatus.PENDING:
                raise svc.InvalidState(f"approval is {a.status.value}")
            await get_redis().set(EDIT_KEY.format(cb.from_user.id), str(aid), ex=600)
            await cb.answer()
            await cb.message.answer("Send the new message text as your next message (or /cancel).")
            return
        elif action == "regen":
            await cb.answer("Regeneration needs the AI engine, which is not implemented yet.", show_alert=True)
            return
        else:
            await cb.answer("Unknown action", show_alert=True)
            return
    except svc.ApprovalNotFound:
        await cb.answer("Not found", show_alert=True)
        return
    except svc.InvalidState as exc:
        await cb.answer(f"Cannot do that: {exc}", show_alert=True)
        return
    if cb.message is not None:
        await show_card(cb.message, session, a, edit=True)


async def capture_edit(message: Message, session, user) -> bool:
    """If the admin is in edit mode, apply this text. Returns True if the message was consumed."""
    r = get_redis()
    key = EDIT_KEY.format(message.from_user.id)
    raw = await r.get(key)
    if raw is None or not message.text:
        return False
    await r.delete(key)
    try:
        a = await svc.edit_text(session, user.id, uuid.UUID(raw), message.text, f"bot:{message.from_user.id}")
    except ValueError:
        await message.answer("Text must be 1-4096 characters. Press EDIT again.")
        return True
    except (svc.InvalidState, svc.ApprovalNotFound) as exc:
        await message.answer(f"Cannot edit: {exc}")
        return True
    await show_card(message, session, a)
    return True
