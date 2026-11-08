import json
import uuid

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as kb
from app.bot.factory import IsAdmin
from app.core.config import admin_ids, get_settings
from app.schemas.person import PersonCreate
from app.services import persons as persons_svc
from app.services.consent_invite import make_token

router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

NOT_BUILT = "This needs the AI engine, which is not implemented yet."
HELP = (
    "/add_person Name [@username] – create a profile\n/people – list profiles\n/profile name – show a profile\n"
    "/note name: text – add a note\n/invite name – consent invitation link for that person\n/delete name – delete a profile and all its data\n"
    "/approvals – messages waiting for your confirmation\n/settings – configuration status\n\n"
    "Not available yet (AI engine): /analyze /topics /common /questions /conversation /group_topics /history /search"
)


async def start_admin(message: Message, user) -> None:
    await message.answer("SOCIAL INTELLIGENCE AI – admin menu. /help for commands.", reply_markup=kb.main_menu())


@router.message(CommandStart())
async def cmd_start(message: Message, user):
    await start_admin(message, user)


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP)


async def _resolve_one(message: Message, session, user, query: str):
    if not query.strip():
        await message.answer("Please give a name or id.")
        return None
    found = await persons_svc.find_persons(session, user.id, query)
    if not found:
        await message.answer("No matching person.")
        return None
    if len(found) > 1:
        await message.answer("Several matches, pick one:", reply_markup=kb.people_list(found))
        return None
    return found[0]


def card(p) -> str:
    lines = [p.display_name, f"id: {p.id}"]
    for label, val in (("category", p.category), ("city", p.city), ("country", p.country),
                       ("education", p.education), ("languages", ", ".join(p.languages) or None), ("notes", p.notes)):
        if val:
            lines.append(f"{label}: {val}")
    if p.do_not_profile:
        lines.append("do not profile: yes")
    lines.append("bot chat: " + ("connected" if p.bot_chat_id else "not connected"))
    return "\n".join(lines)


@router.message(Command("add_person"))
async def add_person(message: Message, command: CommandObject, session, user):
    parts = (command.args or "").split()
    username = next((x for x in parts if x.startswith("@")), None)
    name = " ".join(x for x in parts if x != username)
    try:
        data = PersonCreate(display_name=name, telegram_username=username)
    except ValueError:
        await message.answer("Usage: /add_person Name [@username]")
        return
    try:
        p = await persons_svc.create_person(session, user.id, data, f"bot:{message.from_user.id}")
    except persons_svc.DuplicateIdentifier:
        await message.answer("That username is already linked to another profile.")
        return
    await message.answer(f"Created.\n\n{card(p)}\n\nAdding a person is not consent to collect their messages. "
                         "Use /invite to ask them.", reply_markup=kb.person_actions(p.id))


@router.message(Command("people"))
@router.message(F.text == "PERSONS")
async def people(message: Message, session, user):
    items, total = await persons_svc.list_persons(session, user.id, q=None, category=None, sort="display_name",
                                                  descending=False, limit=25, offset=0)
    if not items:
        await message.answer("No profiles yet. Use /add_person Name [@username].")
        return
    await message.answer(f"{total} profile(s):", reply_markup=kb.people_list(items))


@router.message(Command("profile"))
async def profile(message: Message, command: CommandObject, session, user):
    p = await _resolve_one(message, session, user, command.args or "")
    if p:
        await message.answer(card(p), reply_markup=kb.person_actions(p.id))


@router.message(Command("note"))
async def note(message: Message, command: CommandObject, session, user):
    q, sep, text = (command.args or "").partition(":")
    if not sep or not text.strip():
        await message.answer("Usage: /note name: text")
        return
    p = await _resolve_one(message, session, user, q)
    if p is None:
        return
    new_notes = (p.notes + "\n" if p.notes else "") + text.strip()
    from app.schemas.person import PersonUpdate
    try:
        p = await persons_svc.update_person(session, user.id, p.id, PersonUpdate(notes=new_notes), f"bot:{message.from_user.id}")
    except ValueError:
        await message.answer("Note too long.")
        return
    await message.answer("Note added.\n\n" + card(p))


@router.message(Command("invite"))
async def invite(message: Message, command: CommandObject, session, user, bot):
    p = await _resolve_one(message, session, user, command.args or "")
    if p:
        await message.answer(await _invite_text(bot, p))


async def _invite_text(bot, p) -> str:
    me = await bot.get_me()
    return (f"Send this link to {p.display_name}. They will see what is collected and can agree or decline:\n"
            f"https://t.me/{me.username}?start=c_{make_token(p.id)}")


@router.message(Command("delete"))
async def delete(message: Message, command: CommandObject, session, user):
    p = await _resolve_one(message, session, user, command.args or "")
    if p:
        await message.answer(f"Delete {p.display_name} and all derived data? This cannot be undone.",
                             reply_markup=kb.confirm_delete(p.id))


@router.message(Command("settings"))
@router.message(F.text == "SETTINGS")
async def settings(message: Message):
    s = get_settings()
    await message.answer(
        f"Admin IDs configured: {len(admin_ids())}\nOpenAI key: {'set' if s.openai_api_key.get_secret_value() else 'NOT set'}\n"
        f"Webhook secret: {'set' if s.telegram_webhook_secret.get_secret_value() else 'NOT set'}\n"
        f"Public URL: {s.public_url or 'NOT set'}")


@router.message(Command("analyze", "topics", "common", "questions", "conversation", "group_topics", "history", "search"))
async def not_built(message: Message):
    await message.answer(NOT_BUILT)


@router.message(F.text.in_({"GROUPS", "AI ASSISTANT", "CONVERSATION INTELLIGENCE", "ANALYTICS"}))
async def menu_not_built(message: Message):
    await message.answer(NOT_BUILT)


def _pid(data: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(data.split(":", 2)[2])
    except (IndexError, ValueError):
        return None


@router.callback_query(F.data.startswith("pp:"))
async def person_callbacks(cb: CallbackQuery, session, user, bot):
    action = cb.data.split(":")[1]
    pid = _pid(cb.data)
    found = await persons_svc.find_persons(session, user.id, str(pid)) if pid else []
    if not found or cb.message is None:
        await cb.answer("Not found", show_alert=True)
        return
    p = found[0]
    if action == "view":
        await cb.message.edit_text(card(p), reply_markup=kb.person_actions(p.id))
    elif action == "inv":
        await cb.message.answer(await _invite_text(bot, p))
    elif action == "del":
        await cb.message.edit_text(f"Delete {p.display_name} and all derived data? This cannot be undone.",
                                   reply_markup=kb.confirm_delete(p.id))
    elif action == "delok":
        await persons_svc.delete_person(session, user.id, p.id, f"bot:{cb.from_user.id}")
        await cb.message.edit_text("Deleted.")
    await cb.answer()
