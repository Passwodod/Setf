"""Group-chat handlers. Everything here runs in groups; admin-only commands require a configured admin ID."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot.factory import IsAdmin
from app.models import Group
from app.services import groups as svc

router = Router(name="groups")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))


async def _rights(bot, chat_id: int) -> bool | None:
    """True if the bot can see all messages (privacy mode off or bot is an admin); None if unknown."""
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id, me.id)
        return bool(me.can_read_all_group_messages or member.status in ("administrator", "creator"))
    except Exception:
        return None


@router.message(Command("connect_group"), IsAdmin())
async def connect(message: Message, session, user, bot):
    rights = await _rights(bot, message.chat.id)
    g = await svc.connect_group(session, user.id, message.chat.id, message.chat.title or str(message.chat.id),
                                rights, f"bot:{message.from_user.id}")
    await message.answer(svc.NOTICE)
    await svc.mark_notice_sent(session, g)
    warn = ""
    if rights is False:
        warn = ("\n\nWarning: the bot cannot read normal messages (Telegram privacy mode). Make the bot a group "
                "administrator or disable privacy mode in @BotFather, otherwise only commands are received.")
    elif rights is None:
        warn = "\n\nCould not verify the bot's permissions in this group."
    await message.answer(f"Group connected. Collection is OFF until you send /enable_collection.{warn}")


async def _own_group(message: Message, session, user) -> Group | None:
    for g in await svc.group_by_chat(session, message.chat.id):
        if g.owner_id == user.id:
            return g
    await message.answer("This group is not connected. Use /connect_group first.")
    return None


@router.message(Command("enable_collection"), IsAdmin())
async def enable(message: Message, session, user):
    g = await _own_group(message, session, user)
    if g is None:
        return
    try:
        await svc.enable_collection(session, user.id, g, f"bot:{message.from_user.id}")
    except svc.NoticeRequired as exc:
        await message.answer(str(exc))
        return
    await message.answer("Collection is now ON for this group. Members can /optout at any time.")


@router.message(Command("stop_collection"), IsAdmin())
async def stop(message: Message, session, user):
    g = await _own_group(message, session, user)
    if g:
        await svc.disable_collection(session, user.id, g, f"bot:{message.from_user.id}")
        await message.answer("Collection stopped. Stored data is kept until /purge_group.")


@router.message(Command("purge_group"), IsAdmin())
async def purge(message: Message, session, user):
    g = await _own_group(message, session, user)
    if g:
        n = await svc.purge_messages(session, user.id, g, f"bot:{message.from_user.id}")
        await message.answer(f"Deleted {n} stored message(s) from this group.")


@router.message(Command("optout"))
async def optout(message: Message, session):
    groups = await svc.group_by_chat(session, message.chat.id)
    if not groups or message.from_user is None:
        return
    for g in groups:
        await svc.opt_out(session, g, message.from_user.id)
    await message.answer("Done: your stored messages in this group were deleted and new ones will be ignored. /optin reverses this.")


@router.message(Command("optin"))
async def optin(message: Message, session):
    groups = await svc.group_by_chat(session, message.chat.id)
    if not groups or message.from_user is None:
        return
    for g in groups:
        await svc.opt_in(session, g, message.from_user.id)
    await message.answer("You are included again (only while collection is enabled).")


@router.message(F.text, ~F.text.startswith("/"))
async def collect(message: Message, session):
    if message.from_user is None or message.from_user.is_bot or message.forward_origin is not None:
        return
    for g in await svc.group_by_chat(session, message.chat.id):
        if g.collection_enabled:
            await svc.ingest(session, g, tg_message_id=message.message_id, tg_user_id=message.from_user.id,
                             text=message.text, sent_at=message.date)
