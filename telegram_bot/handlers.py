"""All bot handlers — business API events, commands, and periodic jobs."""

import datetime
import os
import re
import tempfile
from collections import deque

from telegram import Update
from telegram.ext import ContextTypes

import database as db
import ai_client
from config import AUTH_PASSWORD, OWNER_ID

_chat_history: dict[int, deque] = {}
_MAX_HISTORY = 20
_autoreply_sent: set[int] = set()
_authenticated_users: set[int] = set()
_flood_tracker: dict[int, dict[int, list]] = {}

_PERIOD_RE = re.compile(
    r"(\d+)\s*"
    r"(мин(?:ут[аыу]?)?|час(?:а|ов)?|ч\b|м\b|"
    r"д(?:ень|ня|ней)?|сут(?:ки|ок)?|"
    r"недел[яиью]|нед\b|"
    r"месяц(?:а|ев)?|"
    r"год(?:а|ов)?)",
    re.IGNORECASE,
)


def _parse_period(text: str) -> datetime.timedelta | None:
    m = _PERIOD_RE.search(text)
    if not m:
        return None
    num = int(m.group(1))
    unit = m.group(2).lower()
    if unit.startswith("мин") or unit == "м":
        return datetime.timedelta(minutes=num)
    if unit.startswith("час") or unit == "ч":
        return datetime.timedelta(hours=num)
    if unit.startswith("д") or unit.startswith("сут"):
        return datetime.timedelta(days=num)
    if unit.startswith("нед"):
        return datetime.timedelta(weeks=num)
    if unit.startswith("месяц"):
        return datetime.timedelta(days=num * 30)
    if unit.startswith("год"):
        return datetime.timedelta(days=num * 365)
    return None


def _format_timedelta(td: datetime.timedelta) -> str:
    total = int(td.total_seconds())
    if total < 3600:
        return f"{total // 60} мин."
    if total < 86400:
        return f"{total // 3600} ч."
    return f"{total // 86400} дн."


async def _check_admin(chat, user) -> bool:
    if user.id == OWNER_ID:
        return True
    try:
        member = await chat.get_member(user.id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


GROUP_ACCESS_OWNER = "owner"
GROUP_ACCESS_ADMINS = "admins"
GROUP_ACCESS_ALL = "all"
_group_access_level: str = GROUP_ACCESS_ADMINS


def _is_authenticated(user_id: int) -> bool:
    if not AUTH_PASSWORD:
        return True
    if user_id == OWNER_ID:
        return True
    return user_id in _authenticated_users


async def _check_auth(update: Update) -> bool:
    """Check if user is authenticated. Returns True if OK, False if blocked."""
    user = update.effective_user
    if not user:
        return False
    if _is_authenticated(user.id):
        return True
    await update.message.reply_text(
        "Введи пароль для доступа к боту:"
    )
    return False


async def _check_group_access(update: Update) -> bool:
    """Check if user has access in a group chat."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return False
    if chat.type == "private":
        return _is_authenticated(user.id)
    if user.id == OWNER_ID:
        return True
    if _group_access_level == GROUP_ACCESS_ALL:
        return True
    if _group_access_level == GROUP_ACCESS_ADMINS:
        try:
            member = await update.effective_chat.get_member(user.id)
            return member.status in ("administrator", "creator")
        except Exception:
            return False
    return False


def _get_history(chat_id: int) -> list[dict]:
    return list(_chat_history.get(chat_id, []))


def _add_to_history(chat_id: int, role: str, text: str) -> None:
    if chat_id not in _chat_history:
        _chat_history[chat_id] = deque(maxlen=_MAX_HISTORY)
    _chat_history[chat_id].append({"role": role, "content": text})


def _get_media_info(message) -> tuple[str | None, str | None]:
    """Extract media type and file_id from a message."""
    if message.photo:
        return "photo", message.photo[-1].file_id
    if message.video:
        return "video", message.video.file_id
    if message.document:
        return "document", message.document.file_id
    if message.voice:
        return "voice", message.voice.file_id
    if message.video_note:
        return "video_note", message.video_note.file_id
    if message.sticker:
        return "sticker", message.sticker.file_id
    if message.audio:
        return "audio", message.audio.file_id
    if message.animation:
        return "animation", message.animation.file_id
    return None, None


MAX_TG_MSG_LEN = 4096


async def _safe_reply(message, text: str, **kwargs) -> None:
    """Send text, splitting into chunks if it exceeds Telegram's limit."""
    while text:
        chunk = text[:MAX_TG_MSG_LEN]
        text = text[MAX_TG_MSG_LEN:]
        try:
            await message.reply_text(chunk, **kwargs)
        except Exception:
            await message.reply_text(chunk)


MEDIA_LABELS = {
    "photo": "фото",
    "video": "видео",
    "document": "документ",
    "voice": "голосовое",
    "video_note": "кружок",
    "sticker": "стикер",
    "audio": "аудио",
    "animation": "GIF",
}


# ── Business API handlers ────────────────────────────────────────────


_seen_connections: set[str] = set()


async def handle_business_connection(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    conn = update.business_connection
    await db.save_business_connection(
        connection_id=conn.id,
        user_id=conn.user.id,
        user_chat_id=conn.user_chat_id,
        date=conn.date.isoformat() if conn.date else datetime.datetime.now().isoformat(),
        can_reply=1 if conn.can_reply else 0,
    )
    if conn.id in _seen_connections:
        return
    _seen_connections.add(conn.id)
    try:
        await context.bot.send_message(
            chat_id=conn.user.id,
            text=(
                "Бизнес-подключение установлено!\n"
                f"ID: <code>{conn.id}</code>\n"
                f"Могу отвечать: {'Да' if conn.can_reply else 'Нет'}"
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass


async def handle_business_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    msg = update.business_message
    if not msg:
        return

    user = msg.from_user
    media_type, file_id = _get_media_info(msg)

    await db.save_message(
        business_connection_id=getattr(msg, "business_connection_id", None),
        chat_id=msg.chat.id,
        message_id=msg.message_id,
        user_id=user.id if user else None,
        username=user.username if user else None,
        first_name=user.first_name if user else None,
        text=msg.text,
        media_type=media_type,
        file_id=file_id,
        caption=msg.caption,
        date=msg.date.isoformat() if msg.date else datetime.datetime.now().isoformat(),
    )

    if user and user.id != OWNER_ID and (msg.text or media_type):
        await db.save_pending_reply(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            text=(msg.text or msg.caption or f"[{MEDIA_LABELS.get(media_type, 'медиа')}]")[:200],
            date=msg.date.isoformat() if msg.date else datetime.datetime.now().isoformat(),
        )
        ar = await db.get_autoreply(OWNER_ID)
        if ar and ar.get("message"):
            bc_id = getattr(msg, "business_connection_id", None)
            if bc_id and msg.chat.id not in _autoreply_sent:
                _autoreply_sent.add(msg.chat.id)
                try:
                    await context.bot.send_message(
                        chat_id=msg.chat.id,
                        text=ar["message"],
                        business_connection_id=bc_id,
                    )
                except Exception:
                    pass

    if user and user.id == OWNER_ID:
        await db.mark_replied_in_chat(msg.chat.id)


async def handle_edited_business_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    msg = update.edited_business_message
    if not msg:
        return

    user = msg.from_user
    media_type, file_id = _get_media_info(msg)

    await db.save_message(
        business_connection_id=getattr(msg, "business_connection_id", None),
        chat_id=msg.chat.id,
        message_id=msg.message_id,
        user_id=user.id if user else None,
        username=user.username if user else None,
        first_name=user.first_name if user else None,
        text=msg.text,
        media_type=media_type,
        file_id=file_id,
        caption=msg.caption,
        date=msg.date.isoformat() if msg.date else datetime.datetime.now().isoformat(),
    )


async def handle_deleted_business_messages(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    deleted = update.deleted_business_messages
    if not deleted:
        return

    chat_id = deleted.chat.id
    message_ids = list(deleted.message_ids)

    saved_msgs = await db.get_messages_by_ids(chat_id, message_ids)
    await db.mark_deleted(chat_id, message_ids)

    owner_id = OWNER_ID
    if not owner_id or not saved_msgs:
        return

    notification = "<b>Удалённые сообщения:</b>\n\n"
    for msg in saved_msgs:
        name = msg.get("first_name") or "Неизвестный"
        username = f" (@{msg['username']})" if msg.get("username") else ""
        text = msg.get("text") or msg.get("caption") or ""
        media = (
            f" [{MEDIA_LABELS.get(msg['media_type'], msg['media_type'])}]"
            if msg.get("media_type")
            else ""
        )
        date_str = (msg.get("date") or "")[:16].replace("T", " ")

        notification += (
            f"<b>{name}</b>{username}\n"
            f"{date_str}\n"
        )
        if text:
            notification += f"{text}\n"
        if media:
            notification += f"{media}\n"
        notification += "\n"

    try:
        await context.bot.send_message(
            chat_id=owner_id, text=notification, parse_mode="HTML"
        )
    except Exception:
        pass

    for msg in saved_msgs:
        if not msg.get("file_id"):
            continue
        try:
            media_type = msg.get("media_type")
            fid = msg["file_id"]
            cap = "Удалённый контент"
            if media_type == "photo":
                await context.bot.send_photo(owner_id, fid, caption=cap)
            elif media_type == "video":
                await context.bot.send_video(owner_id, fid, caption=cap)
            elif media_type == "document":
                await context.bot.send_document(owner_id, fid, caption=cap)
            elif media_type == "voice":
                await context.bot.send_voice(owner_id, fid)
            elif media_type == "sticker":
                await context.bot.send_sticker(owner_id, fid)
            elif media_type == "audio":
                await context.bot.send_audio(owner_id, fid, caption=cap)
            elif media_type == "animation":
                await context.bot.send_animation(owner_id, fid, caption=cap)
            elif media_type == "video_note":
                await context.bot.send_video_note(owner_id, fid)
        except Exception:
            pass


# ── Command handlers ─────────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if AUTH_PASSWORD and user and not _is_authenticated(user.id):
        await update.message.reply_text(
            "Привет! Этот бот защищён паролем.\n"
            "Введи пароль для доступа:"
        )
        return
    text = (
        "<b>Привет! Я твой бизнес-ассистент.</b>\n\n"
        "Что я умею:\n"
        "- Отслеживаю удалённые сообщения (текст, фото, видео)\n"
        "- Делаю AI-пересказы переписок\n"
        "- Напоминаю кому ответить/позвонить\n"
        "- Приоритетный рейтинг чатов\n"
        "- Распознаю фото и голосовые сообщения\n"
        "- Статистика, поиск, перевод и анализ\n"
        "- Автоответы, заметки, рассылка, шаблоны\n\n"
        "<b>Команды:</b>\n"
        "/deleted — удалённые сообщения\n"
        "/summary — AI-пересказ чата\n"
        "/remind — установить напоминание\n"
        "/pending — кому нужно ответить\n"
        "/priority — приоритет ответов\n"
        "/stats — статистика сообщений\n"
        "/search — поиск по сообщениям\n"
        "/translate — перевод текста\n"
        "/analyze — анализ сообщения\n"
        "/myreminders — мои напоминания\n"
        "/autoreply — автоответ на входящие\n"
        "/note — заметки\n"
        "/broadcast — рассылка всем контактам\n"
        "/contact — инфо о контакте\n"
        "/export — экспорт истории чата\n"
        "/qr — быстрые ответы (шаблоны)\n"
        "/chats — список приватных чатов\n"
        "/groups — список групп\n"
        "/access — настройка доступа в группах\n"
        "/whoami — твой ID и инфо\n"
        "/help — справка\n\n"
        "<b>Команды для групп:</b>\n"
        "/groupstats [часы] — статистика группы\n"
        "/groupsummary [часы] — AI-пересказ группы\n"
        "/top — топ участников группы\n"
        "/groupsearch [запрос] — поиск в группе\n\n"
        "<b>Модерация:</b>\n"
        "/moder on|off — вкл/выкл модерацию\n"
        "/rules — правила группы\n"
        "/warn — предупредить (3 = кик)\n"
        "/mute [мин] — замутить\n"
        "/unmute — размутить\n"
        "/kick — кикнуть\n"
        "/ban — забанить\n"
        "/unban — разбанить\n"
        "/pin — закрепить\n"
        "/unpin — открепить\n"
        "/poll — голосование\n"
        "/report — жалоба админам\n\n"
        "Также можешь просто писать мне — я пойму!\n"
        "Отправь фото — я опишу что на нём.\n"
        "В группе — упомяни меня или ответь на моё сообщение.\n"
        "Подключи меня как бизнес-бота в настройках Telegram!"
    )
    await _safe_reply(update.message, text, parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    text = (
        "<b>Справка по командам:</b>\n\n"
        "/deleted [кол-во] — удалённые сообщения\n"
        "/summary [часы] — AI-пересказ за N часов\n"
        "/remind [кому] [что] [когда] — напоминание\n"
        "/pending — кому нужно ответить\n"
        "/priority — приоритет ответов (срочно/важно/не срочно)\n"
        "/stats [часы] — статистика\n"
        "/search [запрос] — поиск по сообщениям\n"
        "/translate [текст] — перевод\n"
        "/analyze — анализ тона сообщения\n"
        "/myreminders — мои напоминания\n\n"
        "<b>Инструменты:</b>\n\n"
        "/autoreply on [текст] — включить автоответ\n"
        "/autoreply off — выключить автоответ\n"
        "/note save [текст] — сохранить заметку\n"
        "/note list — список заметок\n"
        "/note delete [id] — удалить заметку\n"
        "/broadcast [текст] — рассылка всем\n"
        "/contact @username — инфо о контакте\n"
        "/export [@username] — экспорт чата\n"
        "/qr save/list/use — шаблоны ответов\n"
        "/chats — список приватных чатов (ЛС)\n"
        "/groups — список групп бота\n"
        "/access [owner/admins/all] — доступ в группах\n"
        "/whoami — твой Telegram ID\n\n"
        "<b>Группы:</b>\n\n"
        "/groupstats [часы] — статистика группы\n"
        "/groupsummary [часы] — AI-пересказ группы\n"
        "/top — топ участников группы\n"
        "/groupsearch [запрос] — поиск по группе\n"
        "Упомяни меня @bot или ответь — отвечу на вопрос\n\n"
        "<b>Модерация:</b>\n\n"
        "/moder on — включить модерацию (работать)\n"
        "/moder off — выключить (в сон)\n"
        "/moder welcome [текст] — приветствие ({name} = имя)\n"
        "/moder badwords [слова,через,запятую] — фильтр мата\n"
        "/moder antiflood [макс] [сек] — антифлуд\n"
        "/rules [текст] — правила группы\n"
        "/warn — предупредить (3 = кик)\n"
        "/mute [минуты] — замутить\n"
        "/unmute — размутить\n"
        "/kick [причина] — кикнуть\n"
        "/ban [причина] — забанить\n"
        "/unban — разбанить\n"
        "/pin — закрепить сообщение\n"
        "/unpin — открепить\n"
        "/poll Вопрос | вариант1 | вариант2 — голосование\n"
        "/report — пожаловаться на сообщение\n\n"
        "<b>Фото:</b> отправь фото — я опишу что на нём\n\n"
        "<b>Текстом:</b>\n"
        "• \"Срочно!\" → приоритет чатов\n"
        "• \"Запомни купить молоко\" → заметка\n"
        "• \"Напиши всем привет\" → рассылка\n"
        "• \"Расскажи про @ivan\" → инфо\n"
        "• \"Выключи автоответчик\" → автоответ\n"
    )
    await _safe_reply(update.message, text, parse_mode="HTML")


async def cmd_deleted(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    limit = 10
    if context.args:
        try:
            limit = min(int(context.args[0]), 50)
        except ValueError:
            pass

    messages = await db.get_deleted_messages(limit)

    if not messages:
        await update.message.reply_text("Удалённых сообщений пока нет.")
        return

    text = "<b>Последние удалённые сообщения:</b>\n\n"
    for msg in messages:
        name = msg["first_name"] or "Неизвестный"
        username = f" @{msg['username']}" if msg.get("username") else ""
        uid = msg.get("user_id") or ""
        content = msg.get("text") or msg.get("caption") or ""
        media = (
            f" [{MEDIA_LABELS.get(msg['media_type'], msg['media_type'])}]"
            if msg.get("media_type")
            else ""
        )
        date_str = (msg.get("deleted_at") or "")[:16].replace("T", " ")

        text += f"👤 <b>{name}</b>{username}\n"
        text += f"🆔 <code>{uid}</code>\n"
        text += f"Удалено: {date_str}\n"
        if content:
            text += f"{content[:200]}\n"
        if media:
            text += f"{media}\n"
        text += "\n"

    await _safe_reply(update.message, text, parse_mode="HTML")


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    hours = 24
    if context.args:
        try:
            hours = min(int(context.args[0]), 168)
        except ValueError:
            pass

    await update.message.reply_text("Анализирую сообщения...")

    messages = await db.get_recent_messages(chat_id=None, hours=hours)

    if not messages:
        await update.message.reply_text(f"Нет сообщений за последние {hours} ч.")
        return

    summary = await ai_client.summarize_messages(messages)
    text = f"<b>Пересказ за {hours} ч.</b>\n\n{summary}"
    await _safe_reply(update.message, text, parse_mode="HTML")


async def cmd_remind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    if not context.args:
        await update.message.reply_text(
            "<b>Формат:</b>\n"
            "/remind [кому] [что] [когда]\n\n"
            "<b>Примеры:</b>\n"
            "/remind @ivan позвонить 18:00\n"
            "/remind мама перезвонить 30м\n"
            "/remind встреча 2ч",
            parse_mode="HTML",
        )
        return

    raw = " ".join(context.args)
    now = datetime.datetime.now()
    remind_at = None

    minutes_match = re.search(r"(\d+)\s*[мm](?:ин(?:ут)?)?$", raw)
    hours_match = re.search(r"(\d+)\s*[чhн](?:ас(?:а|ов)?)?$", raw)
    clock_match = re.search(r"(\d{1,2}):(\d{2})$", raw)
    days_match = re.search(r"(\d+)\s*[дd](?:н(?:ей|я)?)?$", raw)

    if minutes_match:
        remind_at = now + datetime.timedelta(minutes=int(minutes_match.group(1)))
        raw = raw[: minutes_match.start()].strip()
    elif hours_match:
        remind_at = now + datetime.timedelta(hours=int(hours_match.group(1)))
        raw = raw[: hours_match.start()].strip()
    elif clock_match:
        hour, minute = int(clock_match.group(1)), int(clock_match.group(2))
        remind_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if remind_at <= now:
            remind_at += datetime.timedelta(days=1)
        raw = raw[: clock_match.start()].strip()
    elif days_match:
        remind_at = now + datetime.timedelta(days=int(days_match.group(1)))
        raw = raw[: days_match.start()].strip()
    else:
        remind_at = now + datetime.timedelta(hours=1)

    target = ""
    if raw.startswith("@"):
        parts = raw.split(maxsplit=1)
        target = parts[0]
        raw = parts[1] if len(parts) > 1 else "напоминание"

    await db.add_reminder(
        owner_id=update.effective_user.id,
        chat_id=update.effective_chat.id,
        target_name=target,
        text=raw or "напоминание",
        remind_at=remind_at.isoformat(),
    )

    target_str = f" для {target}" if target else ""
    await update.message.reply_text(
        f"Напоминание установлено{target_str}:\n"
        f"{raw or 'напоминание'}\n"
        f"Время: {remind_at.strftime('%d.%m %H:%M')}",
    )


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    pending = await db.get_pending_replies(limit=15)

    if not pending:
        await update.message.reply_text("Нет неотвеченных сообщений!")
        return

    text = "<b>Ожидают ответа:</b>\n\n"
    for msg in pending:
        name = msg["first_name"] or "Неизвестный"
        username = f" @{msg['username']}" if msg.get("username") else ""
        uid = msg.get("user_id") or ""
        content = (msg.get("text") or "")[:100]
        date_str = (msg.get("date") or "")[:16].replace("T", " ")

        text += f"👤 <b>{name}</b>{username}\n"
        text += f"🆔 <code>{uid}</code>\n"
        text += f"📅 {date_str}\n"
        if content:
            text += f"💬 {content}\n"
        text += "\n"

    await _safe_reply(update.message, text, parse_mode="HTML")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    hours = 24
    if context.args:
        try:
            hours = int(context.args[0])
        except ValueError:
            pass

    stats = await db.get_message_stats(hours)

    if not stats:
        await update.message.reply_text(f"Нет данных за последние {hours} ч.")
        return

    text = f"<b>Статистика за {hours} ч.</b>\n\n"
    for s in stats:
        name = s["first_name"] or "Неизвестный"
        username = f" (@{s['username']})" if s.get("username") else ""
        text += (
            f"<b>{name}</b>{username}\n"
            f"   Сообщений: {s['msg_count']}\n"
            f"   Медиа: {s['media_count']}\n"
            f"   Удалено: {s['deleted_count']}\n\n"
        )

    await _safe_reply(update.message, text, parse_mode="HTML")


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    if not context.args:
        await update.message.reply_text("Использование: /search [запрос]")
        return

    query = " ".join(context.args)
    results = await db.search_messages(query, limit=10)

    if not results:
        await update.message.reply_text(f'По запросу "{query}" ничего не найдено.')
        return

    text = f'<b>Результаты поиска "{query}":</b>\n\n'
    for msg in results:
        name = msg["first_name"] or "Неизвестный"
        content = msg.get("text") or msg.get("caption") or "[медиа]"
        date_str = (msg.get("date") or "")[:16].replace("T", " ")
        deleted_mark = " [удалено]" if msg["is_deleted"] else ""

        text += f"<b>{name}</b> | {date_str}{deleted_mark}\n"
        text += f"{content[:150]}\n\n"

    await _safe_reply(update.message, text, parse_mode="HTML")


async def cmd_translate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    text_to_translate = ""

    if update.message.reply_to_message:
        text_to_translate = (
            update.message.reply_to_message.text
            or update.message.reply_to_message.caption
            or ""
        )
    elif context.args:
        text_to_translate = " ".join(context.args)

    if not text_to_translate:
        await update.message.reply_text(
            "Ответь на сообщение или: /translate [текст]"
        )
        return

    await update.message.reply_text("Перевожу...")
    translation = await ai_client.translate_text(text_to_translate)
    await _safe_reply(
        update.message, f"<b>Перевод:</b>\n\n{translation}", parse_mode="HTML"
    )


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    text_to_analyze = ""

    if update.message.reply_to_message:
        text_to_analyze = (
            update.message.reply_to_message.text
            or update.message.reply_to_message.caption
            or ""
        )
    elif context.args:
        text_to_analyze = " ".join(context.args)

    if not text_to_analyze:
        await update.message.reply_text(
            "Ответь на сообщение или: /analyze [текст]"
        )
        return

    await update.message.reply_text("Анализирую...")
    analysis = await ai_client.analyze_message(text_to_analyze)
    await _safe_reply(
        update.message, f"<b>Анализ:</b>\n\n{analysis}", parse_mode="HTML"
    )


async def cmd_myreminders(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not await _check_auth(update):
        return
    reminders = await db.get_pending_reminders(update.effective_user.id)

    if not reminders:
        await update.message.reply_text("Активных напоминаний нет.")
        return

    text = "<b>Активные напоминания:</b>\n\n"
    for r in reminders:
        target = f" -> {r['target_name']}" if r.get("target_name") else ""
        time_str = (r.get("remind_at") or "")[:16].replace("T", " ")
        text += f"{time_str}{target}\n{r['reminder_text']}\n\n"

    await _safe_reply(update.message, text, parse_mode="HTML")


# ── Auto-reply command ───────────────────────────────────────────────


async def cmd_autoreply(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not await _check_auth(update):
        return
    if not context.args:
        ar = await db.get_autoreply(OWNER_ID)
        if ar:
            await update.message.reply_text(
                f"<b>Автоответ включён</b>\n\nСообщение: {ar['message']}",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                "<b>Автоответ выключен</b>\n\n"
                "Включить: /autoreply on Я сейчас занят\n"
                "Выключить: /autoreply off",
                parse_mode="HTML",
            )
        return

    action = context.args[0].lower()

    if action == "off":
        await db.set_autoreply(OWNER_ID, 0, "")
        _autoreply_sent.clear()
        await update.message.reply_text("Автоответ выключен.")
        return

    if action == "on":
        message = " ".join(context.args[1:]) if len(context.args) > 1 else "Я сейчас занят, отвечу позже."
        await db.set_autoreply(OWNER_ID, 1, message)
        _autoreply_sent.clear()
        await update.message.reply_text(
            f"<b>Автоответ включён!</b>\n\nСообщение: {message}",
            parse_mode="HTML",
        )
        return

    message = " ".join(context.args)
    await db.set_autoreply(OWNER_ID, 1, message)
    _autoreply_sent.clear()
    await update.message.reply_text(
        f"<b>Автоответ включён!</b>\n\nСообщение: {message}",
        parse_mode="HTML",
    )


# ── Notes command ────────────────────────────────────────────────────


async def cmd_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    if not context.args:
        await update.message.reply_text(
            "<b>Заметки:</b>\n\n"
            "/note save [текст] — сохранить заметку\n"
            "/note list — список заметок\n"
            "/note delete [id] — удалить заметку\n"
            "/note search [запрос] — найти заметку",
            parse_mode="HTML",
        )
        return

    action = context.args[0].lower()

    if action == "save" and len(context.args) > 1:
        text = " ".join(context.args[1:])
        note_id = await db.add_note(OWNER_ID, text)
        await update.message.reply_text(
            f"Заметка #{note_id} сохранена!",
        )
    elif action == "list":
        notes = await db.get_notes(OWNER_ID)
        if not notes:
            await update.message.reply_text("Заметок пока нет.")
            return
        text = "<b>Мои заметки:</b>\n\n"
        for n in notes:
            date_str = (n.get("created_at") or "")[:16].replace("T", " ")
            text += f"<b>#{n['id']}</b> ({date_str})\n{n['text'][:200]}\n\n"
        await _safe_reply(update.message, text, parse_mode="HTML")
    elif action == "delete" and len(context.args) > 1:
        try:
            note_id = int(context.args[1])
        except ValueError:
            await update.message.reply_text("Укажи номер заметки: /note delete 5")
            return
        deleted = await db.delete_note(OWNER_ID, note_id)
        if deleted:
            await update.message.reply_text(f"Заметка #{note_id} удалена.")
        else:
            await update.message.reply_text(f"Заметка #{note_id} не найдена.")
    elif action == "search" and len(context.args) > 1:
        query = " ".join(context.args[1:])
        notes = await db.search_notes(OWNER_ID, query)
        if not notes:
            await update.message.reply_text(f'По запросу "{query}" заметок не найдено.')
            return
        text = f'<b>Заметки по "{query}":</b>\n\n'
        for n in notes:
            date_str = (n.get("created_at") or "")[:16].replace("T", " ")
            text += f"<b>#{n['id']}</b> ({date_str})\n{n['text'][:200]}\n\n"
        await _safe_reply(update.message, text, parse_mode="HTML")
    else:
        await update.message.reply_text(
            "Использование: /note save|list|delete|search [аргумент]"
        )


# ── Broadcast command ────────────────────────────────────────────────


async def cmd_broadcast(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not await _check_auth(update):
        return
    if not context.args:
        await update.message.reply_text(
            "<b>Рассылка:</b>\n/broadcast [текст]\n\n"
            "Отправит сообщение всем контактам через Business API.",
            parse_mode="HTML",
        )
        return

    text = " ".join(context.args)
    chats = await db.get_all_business_chats()

    if not chats:
        await update.message.reply_text(
            "Нет доступных бизнес-чатов для рассылки."
        )
        return

    filtered = [c for c in chats if c.get("user_id") != OWNER_ID]
    if not filtered:
        await update.message.reply_text("Нет контактов для рассылки (кроме себя).")
        return

    status = await update.message.reply_text(
        f"Рассылка: 0/{len(filtered)} отправлено..."
    )
    sent = 0
    errors = 0
    for c in filtered:
        try:
            await context.bot.send_message(
                chat_id=c["chat_id"],
                text=text,
                business_connection_id=c["connection_id"],
            )
            sent += 1
        except Exception:
            errors += 1

    result = f"<b>Рассылка завершена!</b>\n\nОтправлено: {sent}\n"
    if errors:
        result += f"Ошибок: {errors}\n"
    result += f"\nТекст: {text}"
    await status.edit_text(result, parse_mode="HTML")


# ── Contact info command ─────────────────────────────────────────────


async def cmd_contact(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not await _check_auth(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Использование: /contact @username"
        )
        return

    username = context.args[0].lstrip("@")
    stats = await db.get_contact_stats(username)

    if not stats:
        await update.message.reply_text(f"Контакт @{username} не найден в базе.")
        return

    first_seen = (stats.get("first_seen") or "")[:16].replace("T", " ")
    last_seen = (stats.get("last_seen") or "")[:16].replace("T", " ")
    name = stats.get("first_name") or "Неизвестный"

    uid = stats.get("user_id") or ""
    text = (
        f"👤 <b>Контакт: {name}</b> @{username}\n"
        f"🆔 ID: <code>{uid}</code>\n\n"
        f"Всего сообщений: {stats['total_messages']}\n"
        f"Медиа: {stats['media_count']}\n"
        f"Удалённых: {stats['deleted_count']}\n"
        f"Первое сообщение: {first_seen}\n"
        f"Последнее: {last_seen}"
    )
    await _safe_reply(update.message, text, parse_mode="HTML")


# ── Export command ────────────────────────────────────────────────────


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    chat_id = None
    if context.args:
        username = context.args[0].lstrip("@")
        contact = await db.find_chat_by_username(username)
        if contact:
            chat_id = contact["chat_id"]

    messages = await db.export_chat_messages(chat_id=chat_id, limit=500)

    if not messages:
        await update.message.reply_text("Нет сообщений для экспорта.")
        return

    lines = []
    for msg in messages:
        name = msg.get("first_name") or "Неизвестный"
        username_str = f" (@{msg['username']})" if msg.get("username") else ""
        date_str = (msg.get("date") or "")[:19].replace("T", " ")
        content = msg.get("text") or msg.get("caption") or f"[{msg.get('media_type', 'медиа')}]"
        deleted = " [УДАЛЕНО]" if msg.get("is_deleted") else ""
        lines.append(f"[{date_str}] {name}{username_str}{deleted}: {content}")

    export_text = "\n".join(lines)
    if len(export_text) > MAX_TG_MSG_LEN * 3:
        chunks = []
        current = "<b>Экспорт сообщений:</b>\n\n<pre>"
        for line in lines:
            if len(current) + len(line) + 10 > MAX_TG_MSG_LEN:
                chunks.append(current + "</pre>")
                current = "<pre>"
            current += line + "\n"
        if current != "<pre>":
            chunks.append(current + "</pre>")
        for chunk in chunks:
            await _safe_reply(update.message, chunk, parse_mode="HTML")
    else:
        text = f"<b>Экспорт ({len(messages)} сообщений):</b>\n\n<pre>{export_text}</pre>"
        await _safe_reply(update.message, text, parse_mode="HTML")


# ── Quick replies command ────────────────────────────────────────────


async def cmd_qr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    if not context.args:
        await update.message.reply_text(
            "<b>Быстрые ответы:</b>\n\n"
            "/qr save [имя] [текст] — сохранить шаблон\n"
            "/qr list — список шаблонов\n"
            "/qr use [имя] — показать текст шаблона\n"
            "/qr delete [имя] — удалить шаблон",
            parse_mode="HTML",
        )
        return

    action = context.args[0].lower()

    if action == "save" and len(context.args) > 2:
        name = context.args[1]
        text = " ".join(context.args[2:])
        await db.save_quick_reply(OWNER_ID, name, text)
        await update.message.reply_text(f"Шаблон «{name}» сохранён!")
    elif action == "list":
        qrs = await db.get_quick_replies(OWNER_ID)
        if not qrs:
            await update.message.reply_text("Шаблонов пока нет.")
            return
        text = "<b>Быстрые ответы:</b>\n\n"
        for q in qrs:
            text += f"<b>{q['name']}</b>: {q['text'][:100]}\n"
        await _safe_reply(update.message, text, parse_mode="HTML")
    elif action == "use" and len(context.args) > 1:
        name = context.args[1]
        qr = await db.get_quick_reply(OWNER_ID, name)
        if qr:
            await update.message.reply_text(qr["text"])
        else:
            await update.message.reply_text(f"Шаблон «{name}» не найден.")
    elif action == "delete" and len(context.args) > 1:
        name = context.args[1]
        deleted = await db.delete_quick_reply(OWNER_ID, name)
        if deleted:
            await update.message.reply_text(f"Шаблон «{name}» удалён.")
        else:
            await update.message.reply_text(f"Шаблон «{name}» не найден.")
    else:
        await update.message.reply_text(
            "Использование: /qr save|list|use|delete [аргумент]"
        )


# ── Free-form AI Q&A handler ─────────────────────────────────────────


async def handle_direct_question(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """AI agent that classifies intent and uses appropriate tools."""
    msg = update.message
    if not msg or not msg.text:
        return

    if msg.text.startswith("/"):
        return

    user = update.effective_user
    chat = update.effective_chat

    if AUTH_PASSWORD and user and not _is_authenticated(user.id):
        if msg.text.strip() == AUTH_PASSWORD:
            _authenticated_users.add(user.id)
            await msg.reply_text(
                "\U0001f513 Доступ разрешён! Напиши /start чтобы увидеть команды."
            )
            return
        await msg.reply_text("Неверный пароль. Попробуй ещё.")
        return

    if chat and chat.type != "private":
        if not await _check_group_access(update):
            return

    chat_id = msg.chat.id
    _add_to_history(chat_id, "user", msg.text)

    await context.bot.send_chat_action(chat_id, "typing")

    intent = ai_client.classify_intent(msg.text)

    if intent == "DIALOG_READ":
        await _handle_dialog_read(msg, chat_id)
    elif intent == "MESSAGE_SEND":
        await _handle_message_send(msg, context, chat_id)
    elif intent == "SCHEDULE_MESSAGE":
        await _handle_schedule_message(msg, context, chat_id)
    elif intent == "BROADCAST":
        await _handle_broadcast_nl(msg, context, chat_id)
    elif intent == "NOTE":
        await _handle_note_nl(msg, chat_id)
    elif intent == "CONTACT_INFO":
        await _handle_contact_nl(msg, chat_id)
    elif intent == "AUTOREPLY":
        await _handle_autoreply_nl(msg, chat_id)
    elif intent == "QUICK_REPLY":
        await _handle_qr_nl(msg, chat_id)
    elif intent == "DELETED":
        await _handle_deleted_nl(msg, chat_id)
    elif intent == "PENDING":
        await _handle_pending_nl(msg, chat_id)
    elif intent == "STATS":
        await _handle_stats_nl(msg, chat_id)
    elif intent == "REMIND_LIST":
        await _handle_remind_list_nl(msg, chat_id)
    elif intent == "REMIND_SET":
        await _handle_remind_set_nl(msg, chat_id)
    elif intent == "SEARCH":
        await _handle_search_nl(msg, chat_id)
    elif intent == "EXPORT":
        await _handle_export_nl(msg, chat_id)
    elif intent == "SUMMARY":
        await _handle_summary_nl(msg, chat_id)
    elif intent == "PRIORITY":
        await _handle_priority_nl(msg, chat_id)
    elif intent == "GROUP_LIST":
        await _handle_group_list_nl(msg, chat_id)
    elif intent == "MODERATION_REMOTE":
        await _handle_moderation_remote_nl(msg, context, chat_id)
    elif intent == "FILE_GENERATE":
        await _handle_file_generate(msg, context, chat_id)
    else:
        is_group = chat and chat.type != "private"
        status_msg = await msg.reply_text("💭 Думаю...")
        await context.bot.send_chat_action(chat_id, "typing")
        history = _get_history(chat_id)
        answer = await ai_client.answer_question(
            msg.text, history=history[:-1], is_group=is_group,
        )
        _add_to_history(chat_id, "assistant", answer)
        try:
            await status_msg.edit_text(answer)
        except Exception:
            await _safe_reply(msg, answer)


async def _handle_dialog_read(msg, chat_id: int) -> None:
    status_msg = await msg.reply_text(
        "Сейчас отвечу на вопрос\n"
        "Tools:\n  Dialog_read"
    )

    messages = await db.get_recent_messages(chat_id=None, hours=24)

    if not messages:
        await status_msg.edit_text(
            "Сейчас отвечу на вопрос\n"
            "Tools:\n  Dialog_read — нет сообщений за последние 24ч"
        )
        return

    await status_msg.edit_text(
        "Сейчас отвечу на вопрос\n"
        f"Tools:\n  Dialog_read — прочитано {len(messages)} сообщений\n"
        "  AI_analyze"
    )

    answer = await ai_client.answer_with_dialog(msg.text, messages)
    _add_to_history(chat_id, "assistant", answer)
    await status_msg.edit_text(
        "Сейчас отвечу на вопрос\n"
        f"Tools:\n  Dialog_read — прочитано {len(messages)} сообщений ✓\n"
        "  AI_analyze ✓"
    )
    await _safe_reply(msg, answer)


def _extract_username(text: str) -> str | None:
    m = re.search(r"@(\w+)", text)
    return m.group(1) if m else None


_GROUP_TARGET_WORDS = re.compile(
    r"в\s+групп[уеа]|группу|group", re.IGNORECASE
)


async def _handle_message_send(
    msg, context: ContextTypes.DEFAULT_TYPE, chat_id: int
) -> None:
    status_msg = await msg.reply_text(
        "Отправляю сообщение\n"
        "Tools:\n  Compose_message"
    )

    composed = await ai_client.compose_message(msg.text)

    await status_msg.edit_text(
        "Отправляю сообщение\n"
        "Tools:\n  Compose_message ✓\n"
        "  Chat_lookup"
    )

    is_group_target = bool(_GROUP_TARGET_WORDS.search(msg.text))

    target_username = _extract_username(msg.text)
    target_chat = None
    group_candidates = []

    if is_group_target:
        groups = await db.get_known_group_chats()
        if groups:
            target_chat = groups[0]
            group_candidates = groups

    if not target_chat and target_username:
        target_chat = await db.find_chat_by_username(target_username)

    if not target_chat and not is_group_target:
        chats = await db.get_known_chats()
        for c in chats:
            if c["chat_id"] != chat_id and c.get("user_id") != OWNER_ID:
                target_chat = c
                break

    if not target_chat:
        target_label = f"@{target_username}" if target_username else ("группу" if is_group_target else "получатель")
        _add_to_history(chat_id, "assistant", f"Не найден чат {target_label}. Текст: {composed}")
        await status_msg.edit_text(
            "Отправляю сообщение\n"
            "Tools:\n  Compose_message ✓\n"
            f"  Chat_lookup — не найден чат {target_label}\n\n"
            + ("Добавь бота в группу и напиши там хотя бы одно сообщение." if is_group_target
               else "Пользователь должен сначала написать тебе, чтобы бот увидел его через Business API.")
        )
        return

    send_chat_id = target_chat["chat_id"]
    chat_name = target_chat.get("first_name") or f"@{target_username or send_chat_id}"
    is_group = send_chat_id < 0

    if not is_group:
        bc = await db.get_business_connection_for_chat(send_chat_id)
        if not bc:
            bc = await db.get_any_business_connection()
        if not bc:
            _add_to_history(chat_id, "assistant", f"Нет бизнес-подключения. Текст: {composed}")
            await status_msg.edit_text(
                "Отправляю сообщение\n"
                "Tools:\n  Compose_message ✓\n"
                "  Chat_lookup — нет бизнес-подключения\n\n"
                "Подключи бота как бизнес-бота в настройках Telegram."
            )
            return

    sent = False
    last_err = None
    if is_group:
        candidates = [target_chat] + [g for g in group_candidates if g["chat_id"] != send_chat_id]
        for candidate in candidates:
            try:
                await context.bot.send_message(chat_id=candidate["chat_id"], text=composed)
                chat_name = candidate.get("first_name") or str(candidate["chat_id"])
                send_chat_id = candidate["chat_id"]
                sent = True
                break
            except Exception as e:
                last_err = e
                continue
    else:
        try:
            await context.bot.send_message(
                chat_id=send_chat_id,
                text=composed,
                business_connection_id=bc["connection_id"],
            )
            sent = True
        except Exception as e:
            last_err = e

    if sent:
        _add_to_history(chat_id, "assistant", f"Отправлено {chat_name}: {composed}")
        await status_msg.edit_text(
            "Отправляю сообщение\n"
            "Tools:\n  Compose_message ✓\n"
            f"  Chat_lookup → {chat_name} ✓\n"
            f"  Message_send ✓\n\n"
            f"Отправлено: {composed}"
        )
    else:
        _add_to_history(chat_id, "assistant", f"Ошибка отправки: {last_err}")
        hint = ""
        if is_group and "not a member" in str(last_err):
            hint = "\n\nБот не является участником группы. Добавь бота в группу как участника."
        await status_msg.edit_text(
            "Отправляю сообщение\n"
            "Tools:\n  Compose_message ✓\n"
            f"  Chat_lookup → {chat_name} ✓\n"
            f"  Message_send — ошибка: {last_err}{hint}\n\n"
            f"Текст: {composed}"
        )


async def _handle_schedule_message(
    msg, context: ContextTypes.DEFAULT_TYPE, chat_id: int
) -> None:
    status_msg = await msg.reply_text(
        "Планирую сообщение\n"
        "Tools:\n  Parse_time"
    )

    now = datetime.datetime.now()
    raw = msg.text
    send_at = None

    minutes_match = re.search(r"(\d+)\s*[мm](?:ин(?:ут)?)?", raw)
    hours_match = re.search(r"(\d+)\s*[чhн](?:ас(?:а|ов)?)?", raw)
    clock_match = re.search(r"(\d{1,2}):(\d{2})", raw)

    if clock_match:
        hour, minute = int(clock_match.group(1)), int(clock_match.group(2))
        send_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if send_at <= now:
            send_at += datetime.timedelta(days=1)
    elif hours_match:
        send_at = now + datetime.timedelta(hours=int(hours_match.group(1)))
    elif minutes_match:
        send_at = now + datetime.timedelta(minutes=int(minutes_match.group(1)))
    else:
        send_at = now + datetime.timedelta(hours=1)

    await status_msg.edit_text(
        "Планирую сообщение\n"
        "Tools:\n  Parse_time ✓\n"
        "  Compose_message"
    )

    composed = await ai_client.compose_message(raw)

    await status_msg.edit_text(
        "Планирую сообщение\n"
        "Tools:\n  Parse_time ✓\n"
        "  Compose_message ✓\n"
        "  Chat_lookup"
    )

    is_group_target = bool(_GROUP_TARGET_WORDS.search(msg.text))
    target_username = _extract_username(msg.text)
    target_chat = None

    if is_group_target:
        groups = await db.get_known_group_chats()
        if groups:
            target_chat = groups[0]

    if not target_chat and target_username:
        target_chat = await db.find_chat_by_username(target_username)

    if not target_chat and not is_group_target:
        chats = await db.get_known_chats()
        for c in chats:
            if c["chat_id"] != chat_id and c.get("user_id") != OWNER_ID:
                target_chat = c
                break

    if not target_chat:
        target_label = f"@{target_username}" if target_username else ("группу" if is_group_target else "получатель")
        _add_to_history(chat_id, "assistant", f"Не найден чат {target_label}. Текст: {composed}")
        await status_msg.edit_text(
            "Планирую сообщение\n"
            "Tools:\n  Parse_time ✓\n"
            "  Compose_message ✓\n"
            f"  Chat_lookup — не найден чат {target_label}\n\n"
            + ("Добавь бота в группу и напиши там хотя бы одно сообщение." if is_group_target
               else "Пользователь должен сначала написать тебе, чтобы бот увидел его через Business API.")
        )
        return

    target_chat_id = target_chat["chat_id"]
    is_group = target_chat_id < 0
    bc = None
    if not is_group:
        bc = await db.get_business_connection_for_chat(target_chat_id)
        if not bc:
            bc = await db.get_any_business_connection()

    await db.add_scheduled_message(
        owner_id=OWNER_ID,
        chat_id=target_chat_id,
        business_connection_id=bc["connection_id"] if bc else None,
        text=composed,
        send_at=send_at.isoformat(),
    )

    chat_name = target_chat.get("first_name") or f"@{target_username or target_chat_id}"
    _add_to_history(
        chat_id, "assistant",
        f"Запланировано для {chat_name} на {send_at.strftime('%d.%m %H:%M')}: {composed}",
    )
    await status_msg.edit_text(
        "Планирую сообщение\n"
        "Tools:\n  Parse_time ✓\n"
        "  Compose_message ✓\n"
        f"  Chat_lookup → {chat_name} ✓\n"
        f"  Schedule_message ✓ → {send_at.strftime('%d.%m %H:%M')}\n\n"
        f"Запланировано: {composed}\n"
        f"Получатель: {chat_name}\n"
        f"Время: {send_at.strftime('%d.%m.%Y %H:%M')}"
    )


# ── Natural language handlers for new tools ──────────────────────────


async def _handle_note_nl(msg, chat_id: int) -> None:
    """Handle note-related requests from natural language."""
    text = msg.text.lower()
    status_msg = await msg.reply_text("Заметки\nTools:\n  Notes_manager")

    if re.search(r"удали\s+заметк", text, re.IGNORECASE):
        m = re.search(r"#?(\d+)", msg.text)
        if m:
            note_id = int(m.group(1))
            deleted = await db.delete_note(OWNER_ID, note_id)
            result = f"Заметка #{note_id} удалена." if deleted else f"Заметка #{note_id} не найдена."
        else:
            result = "Укажи номер заметки для удаления, например: удали заметку #5"
        _add_to_history(chat_id, "assistant", result)
        await status_msg.edit_text(
            "Заметки\nTools:\n  Notes_manager ✓\n\n" + result
        )
        return

    if re.search(r"(?:мои|покажи|список)\s+заметк|что\s+(?:я\s+)?записывал", text, re.IGNORECASE):
        notes = await db.get_notes(OWNER_ID)
        if not notes:
            result = "Заметок пока нет."
        else:
            lines = []
            for n in notes:
                date_str = (n.get("created_at") or "")[:16].replace("T", " ")
                lines.append(f"#{n['id']} ({date_str}): {n['text'][:100]}")
            result = "Твои заметки:\n" + "\n".join(lines)
        _add_to_history(chat_id, "assistant", result)
        await status_msg.edit_text(
            f"Заметки\nTools:\n  Notes_manager — {len(notes) if notes else 0} заметок ✓\n\n{result}"
        )
        return

    raw = msg.text
    clean = re.sub(
        r"^(?:запомни|запиши|сохрани\s+(?:заметку|запись))\s*(?:что\s+)?",
        "", raw, flags=re.IGNORECASE,
    ).strip()
    if not clean:
        clean = raw
    note_id = await db.add_note(OWNER_ID, clean)
    result = f"Заметка #{note_id} сохранена: {clean}"
    _add_to_history(chat_id, "assistant", result)
    await status_msg.edit_text(
        f"Заметки\nTools:\n  Notes_save ✓\n\n{result}"
    )


async def _handle_broadcast_nl(
    msg, context: ContextTypes.DEFAULT_TYPE, chat_id: int
) -> None:
    """Handle broadcast requests from natural language."""
    raw = msg.text.strip()
    composed = re.sub(
        r"^(?:рассылк[аиу]|(?:напиши|отправь|пошли)\s+всем)\s*",
        "", raw, flags=re.IGNORECASE,
    ).strip()
    if not composed:
        await msg.reply_text("Укажи текст рассылки.\nПример: рассылка Привет всем!")
        return

    status_msg = await msg.reply_text(
        f"Рассылка\nTools:\n  Broadcast_send → \"{composed[:40]}...\""
        if len(composed) > 40
        else f"Рассылка\nTools:\n  Broadcast_send → \"{composed}\""
    )

    chats = await db.get_all_business_chats()
    filtered = [c for c in chats if c.get("user_id") != OWNER_ID]

    if not filtered:
        _add_to_history(chat_id, "assistant", "Нет контактов для рассылки.")
        await status_msg.edit_text(
            "Рассылка\nTools:\n"
            "  Broadcast_send — нет контактов\n\n"
            "Нет доступных бизнес-контактов для рассылки."
        )
        return

    sent = 0
    for c in filtered:
        try:
            await context.bot.send_message(
                chat_id=c["chat_id"],
                text=composed,
                business_connection_id=c["connection_id"],
            )
            sent += 1
        except Exception:
            pass

    result = f"Отправлено {sent}/{len(filtered)} контактам: {composed}"
    _add_to_history(chat_id, "assistant", result)
    await status_msg.edit_text(
        "Рассылка\nTools:\n"
        f"  Broadcast_send ✓ → {sent}/{len(filtered)}\n\n"
        f"Текст: {composed}"
    )


async def _handle_contact_nl(msg, chat_id: int) -> None:
    """Handle contact info requests from natural language."""
    status_msg = await msg.reply_text(
        "Информация о контакте\nTools:\n  Contact_lookup"
    )

    username = _extract_username(msg.text)
    if not username:
        _add_to_history(chat_id, "assistant", "Укажи @username контакта.")
        await status_msg.edit_text(
            "Информация о контакте\nTools:\n  Contact_lookup — не указан @username\n\n"
            "Укажи @username, например: расскажи про @ivan"
        )
        return

    stats = await db.get_contact_stats(username)
    if not stats:
        _add_to_history(chat_id, "assistant", f"Контакт @{username} не найден.")
        await status_msg.edit_text(
            "Информация о контакте\nTools:\n"
            f"  Contact_lookup — @{username} не найден\n\n"
            f"Контакт @{username} пока не встречался в чатах."
        )
        return

    name = stats.get("first_name") or "Неизвестный"
    first_seen = (stats.get("first_seen") or "")[:16].replace("T", " ")
    last_seen = (stats.get("last_seen") or "")[:16].replace("T", " ")
    result = (
        f"{name} (@{username})\n"
        f"Сообщений: {stats['total_messages']}, "
        f"медиа: {stats['media_count']}, "
        f"удалённых: {stats['deleted_count']}\n"
        f"Первое: {first_seen}, последнее: {last_seen}"
    )
    _add_to_history(chat_id, "assistant", result)
    await status_msg.edit_text(
        "Информация о контакте\nTools:\n"
        f"  Contact_lookup → @{username} ✓\n\n{result}"
    )


async def _handle_autoreply_nl(msg, chat_id: int) -> None:
    """Handle autoreply toggle from natural language."""
    text = msg.text.lower()
    status_msg = await msg.reply_text(
        "Автоответ\nTools:\n  Autoreply_config"
    )

    if re.search(r"(?:выключи|убери|отключи|off|выкл|отключить)", text, re.IGNORECASE):
        await db.set_autoreply(OWNER_ID, 0, "")
        _autoreply_sent.clear()
        result = "Автоответ выключен."
        _add_to_history(chat_id, "assistant", result)
        await status_msg.edit_text(
            "Автоответ\nTools:\n  Autoreply_config ✓\n\n" + result
        )
        return

    ar_text = re.sub(
        r"^(?:включи|установи|поставь|установить|включить)\s+(?:автоответ(?:чик)?)\s*(?:на\s*)?",
        "", msg.text, flags=re.IGNORECASE,
    ).strip()
    if not ar_text or ar_text.lower() == msg.text.lower():
        ar_text = "Я сейчас занят, отвечу позже."

    await db.set_autoreply(OWNER_ID, 1, ar_text)
    _autoreply_sent.clear()
    result = f"Автоответ включён: {ar_text}"
    _add_to_history(chat_id, "assistant", result)
    await status_msg.edit_text(
        "Автоответ\nTools:\n  Autoreply_config ✓\n\n" + result
    )


async def _handle_qr_nl(msg, chat_id: int) -> None:
    """Handle quick reply requests from natural language."""
    text = msg.text.lower()
    status_msg = await msg.reply_text(
        "Быстрые ответы\nTools:\n  QuickReply_manager"
    )

    if re.search(r"(?:мои|покажи|список)\s+(?:шаблон|быстр)", text, re.IGNORECASE):
        qrs = await db.get_quick_replies(OWNER_ID)
        if not qrs:
            result = "Шаблонов пока нет. Создай: /qr save имя текст"
        else:
            lines = [f"• {q['name']}: {q['text'][:80]}" for q in qrs]
            result = "Твои шаблоны:\n" + "\n".join(lines)
        _add_to_history(chat_id, "assistant", result)
        await status_msg.edit_text(
            "Быстрые ответы\nTools:\n  QuickReply_manager ✓\n\n" + result
        )
        return

    _add_to_history(chat_id, "assistant", "Используй /qr save|list|use|delete")
    await status_msg.edit_text(
        "Быстрые ответы\nTools:\n  QuickReply_manager ✓\n\n"
        "Команды:\n"
        "/qr save [имя] [текст]\n"
        "/qr list\n"
        "/qr use [имя]\n"
        "/qr delete [имя]"
    )


# ── Voice message handler ──────────────────────────────────────────


async def handle_voice_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Transcribe voice message and respond with AI."""
    msg = update.message
    if not msg:
        return
    if not await _check_auth(update):
        return

    voice = msg.voice or msg.audio
    if not voice:
        return

    chat_id = msg.chat.id
    status_msg = await msg.reply_text(
        "Распознаю голосовое\nTools:\n  Voice_transcribe"
    )

    try:
        tg_file = await context.bot.get_file(voice.file_id)
        local_path = os.path.join(tempfile.gettempdir(), f"voice_{msg.message_id}.ogg")
        await tg_file.download_to_drive(local_path)
    except Exception as e:
        await status_msg.edit_text(
            f"Распознаю голосовое\nTools:\n  Voice_transcribe — ошибка: {e}"
        )
        return

    transcription = await ai_client.transcribe_voice(local_path)

    try:
        os.remove(local_path)
    except Exception:
        pass

    if not transcription:
        await status_msg.edit_text(
            "Распознаю голосовое\nTools:\n"
            "  Voice_transcribe — не удалось распознать\n\n"
            "Не смог распознать голосовое сообщение. "
            "Установи ffmpeg и SpeechRecognition для распознавания."
        )
        return

    await status_msg.edit_text(
        "Распознаю голосовое\nTools:\n"
        "  Voice_transcribe ✓\n"
        "  AI_response\n\n"
        f"Распознано: {transcription}"
    )

    _add_to_history(chat_id, "user", f"[Голосовое]: {transcription}")
    history = _get_history(chat_id)
    answer = await ai_client.answer_question(transcription, history=history[:-1])
    _add_to_history(chat_id, "assistant", answer)

    await status_msg.edit_text(
        "Распознаю голосовое\nTools:\n"
        "  Voice_transcribe ✓\n"
        "  AI_response ✓\n\n"
        f"Распознано: {transcription}\n\n"
        f"{answer}"
    )


# ── NL handlers for basic commands ───────────────────────────────────


async def _handle_deleted_nl(msg, chat_id: int) -> None:
    status_msg = await msg.reply_text(
        "Удалённые сообщения\nTools:\n  Deleted_lookup"
    )
    messages = await db.get_deleted_messages(10)
    if not messages:
        result = "Удалённых сообщений нет."
    else:
        lines = []
        for m in messages:
            name = m.get("first_name") or "Неизвестный"
            content = m.get("text") or m.get("caption") or ""
            media = (
                f" [{MEDIA_LABELS.get(m['media_type'], m['media_type'])}]"
                if m.get("media_type") else ""
            )
            date_str = (m.get("deleted_at") or "")[:16].replace("T", " ")
            lines.append(f"{name} ({date_str}): {content[:100]}{media}")
        result = "Удалённые:\n" + "\n".join(lines)
    _add_to_history(chat_id, "assistant", result)
    await status_msg.edit_text(
        f"Удалённые сообщения\nTools:\n  Deleted_lookup ✓\n\n{result}"
    )


async def _handle_pending_nl(msg, chat_id: int) -> None:
    status_msg = await msg.reply_text(
        "Неотвеченные\nTools:\n  Pending_check"
    )
    pending = await db.get_pending_replies(limit=15)
    if not pending:
        result = "Нет неотвеченных сообщений!"
    else:
        lines = []
        for p in pending:
            name = p.get("first_name") or "Неизвестный"
            username = f" (@{p['username']})" if p.get("username") else ""
            content = (p.get("text") or "")[:80]
            lines.append(f"{name}{username}: {content}")
        result = "Ожидают ответа:\n" + "\n".join(lines)
    _add_to_history(chat_id, "assistant", result)
    await status_msg.edit_text(
        f"Неотвеченные\nTools:\n  Pending_check ✓\n\n{result}"
    )


async def _handle_stats_nl(msg, chat_id: int) -> None:
    status_msg = await msg.reply_text("Статистика\nTools:\n  Stats_calc")
    stats = await db.get_message_stats(24)
    if not stats:
        result = "Нет данных за последние 24ч."
    else:
        lines = ["Статистика за 24ч:"]
        for s in stats:
            name = s.get("first_name") or "Неизвестный"
            lines.append(f"  {name}: {s['msg_count']} сообщений")
        result = "\n".join(lines)
    _add_to_history(chat_id, "assistant", result)
    await status_msg.edit_text(
        f"Статистика\nTools:\n  Stats_calc ✓\n\n{result}"
    )


async def _handle_remind_list_nl(msg, chat_id: int) -> None:
    status_msg = await msg.reply_text(
        "Напоминания\nTools:\n  Reminder_list"
    )
    reminders = await db.get_pending_reminders(OWNER_ID)
    if not reminders:
        result = "Активных напоминаний нет."
    else:
        lines = []
        for r in reminders:
            target = f" для {r['target_name']}" if r.get("target_name") else ""
            time_str = (r.get("remind_at") or "")[:16].replace("T", " ")
            lines.append(f"  {time_str}{target}: {r['reminder_text']}")
        result = "Активные напоминания:\n" + "\n".join(lines)
    _add_to_history(chat_id, "assistant", result)
    await status_msg.edit_text(
        f"Напоминания\nTools:\n  Reminder_list ✓\n\n{result}"
    )


async def _handle_remind_set_nl(msg, chat_id: int) -> None:
    status_msg = await msg.reply_text(
        "Напоминание\nTools:\n  Reminder_set"
    )
    raw = msg.text
    now = datetime.datetime.now()
    remind_at = None

    minutes_match = re.search(r"через\s+(\d+)\s*(?:мин|м\b)", raw, re.IGNORECASE)
    hours_match = re.search(r"через\s+(\d+)\s*(?:час|ч\b)", raw, re.IGNORECASE)
    clock_match = re.search(r"в\s+(\d{1,2}):(\d{2})", raw)

    if minutes_match:
        remind_at = now + datetime.timedelta(minutes=int(minutes_match.group(1)))
    elif hours_match:
        remind_at = now + datetime.timedelta(hours=int(hours_match.group(1)))
    elif clock_match:
        hour, minute = int(clock_match.group(1)), int(clock_match.group(2))
        remind_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if remind_at <= now:
            remind_at += datetime.timedelta(days=1)
    else:
        remind_at = now + datetime.timedelta(hours=1)

    text = re.sub(
        r"(?:напомни\s+(?:мне\s+)?|поставь\s+напоминание\s*|установи\s+напоминание\s*)",
        "", raw, flags=re.IGNORECASE,
    ).strip()
    text = re.sub(
        r"(?:через\s+\d+\s*(?:мин|час|ч\b|м\b)|в\s+\d{1,2}:\d{2})",
        "", text, flags=re.IGNORECASE,
    ).strip()
    if not text:
        text = "напоминание"

    await db.add_reminder(
        owner_id=OWNER_ID,
        chat_id=chat_id,
        target_name="",
        text=text,
        remind_at=remind_at.isoformat(),
    )

    result = f"Напоминание: {text}\nВремя: {remind_at.strftime('%d.%m %H:%M')}"
    _add_to_history(chat_id, "assistant", result)
    await status_msg.edit_text(
        f"Напоминание\nTools:\n  Reminder_set ✓\n\n{result}"
    )


async def _handle_search_nl(msg, chat_id: int) -> None:
    status_msg = await msg.reply_text("Поиск\nTools:\n  Message_search")
    query = re.sub(
        r"^(?:найди|ищи|поиск)\s+(?:сообщени[яе]?\s+(?:про|о|об|\s+со\s+словом)?\s*|в\s+чат[ае]?\s+)",
        "", msg.text, flags=re.IGNORECASE,
    ).strip()
    if not query:
        query = msg.text

    results = await db.search_messages(query, limit=10)
    if not results:
        result = f"По запросу '{query}' ничего не найдено."
    else:
        lines = []
        for m in results:
            name = m.get("first_name") or "Неизвестный"
            text = (m.get("text") or m.get("caption") or "")[:80]
            date_str = (m.get("date") or "")[:16].replace("T", " ")
            lines.append(f"[{date_str}] {name}: {text}")
        result = f"Найдено {len(results)}:\n" + "\n".join(lines)
    _add_to_history(chat_id, "assistant", result)
    await status_msg.edit_text(
        f"Поиск\nTools:\n  Message_search → '{query}' ✓\n\n{result}"
    )


async def _handle_export_nl(msg, chat_id: int) -> None:
    status_msg = await msg.reply_text("Экспорт\nTools:\n  Chat_export")

    username = _extract_username(msg.text)
    target_chat_id = None
    if username:
        chat_data = await db.find_chat_by_username(username)
        if chat_data:
            target_chat_id = chat_data["chat_id"]

    messages = await db.export_chat_messages(target_chat_id)
    if not messages:
        result = "Нет сообщений для экспорта."
        _add_to_history(chat_id, "assistant", result)
        await status_msg.edit_text(
            f"Экспорт\nTools:\n  Chat_export — нет сообщений\n\n{result}"
        )
        return

    lines = []
    for m in messages[-50:]:
        name = m.get("first_name") or "Неизвестный"
        text = (
            m.get("text") or m.get("caption")
            or f"[{MEDIA_LABELS.get(m.get('media_type', ''), 'медиа')}]"
        )
        date_str = (m.get("date") or "")[:16].replace("T", " ")
        deleted = " [УДАЛЕНО]" if m.get("is_deleted") else ""
        lines.append(f"[{date_str}] {name}: {text}{deleted}")
    result = "\n".join(lines)

    _add_to_history(chat_id, "assistant", f"Экспорт: {len(messages)} сообщений")
    await status_msg.edit_text(
        f"Экспорт\nTools:\n  Chat_export ✓ → {len(messages)} сообщений"
    )
    await _safe_reply(msg, result)


async def _handle_summary_nl(msg, chat_id: int) -> None:
    status_msg = await msg.reply_text(
        "Пересказ\nTools:\n  Summary_generate"
    )
    messages = await db.get_recent_messages(chat_id=None, hours=24)
    if not messages:
        result = "Нет сообщений за последние 24ч."
        _add_to_history(chat_id, "assistant", result)
        await status_msg.edit_text(
            f"Пересказ\nTools:\n  Summary_generate — нет сообщений\n\n{result}"
        )
        return

    await status_msg.edit_text(
        f"Пересказ\nTools:\n  Summary_generate — анализ {len(messages)} сообщений"
    )
    summary = await ai_client.summarize_messages(messages)
    _add_to_history(chat_id, "assistant", summary)
    await status_msg.edit_text(
        f"Пересказ\nTools:\n  Summary_generate ✓\n\n{summary}"
    )


async def _handle_priority_nl(msg, chat_id: int) -> None:
    status_msg = await msg.reply_text(
        "Анализирую чаты\nTools:\n  Pending_check\n  Priority_rank"
    )
    chats = await db.get_pending_with_context(limit_per_chat=5)
    if not chats:
        result = "Нет неотвеченных чатов! Все чисто."
        _add_to_history(chat_id, "assistant", result)
        await status_msg.edit_text(
            f"Анализирую чаты\nTools:\n  Pending_check ✓ → 0 чатов\n\n{result}"
        )
        return

    await status_msg.edit_text(
        f"Анализирую чаты\nTools:\n"
        f"  Pending_check ✓ → {len(chats)} чатов\n"
        f"  Priority_rank — анализ..."
    )
    ranking = await ai_client.rank_chat_priority(chats)
    _add_to_history(chat_id, "assistant", ranking)
    await status_msg.edit_text(
        f"Анализирую чаты\nTools:\n"
        f"  Pending_check ✓ → {len(chats)} чатов\n"
        f"  Priority_rank ✓\n\n{ranking}"
    )


async def cmd_priority(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show priority-ranked list of chats that need a reply."""
    if not await _check_auth(update):
        return
    msg = update.message
    status_msg = await msg.reply_text(
        "Анализирую чаты\nTools:\n  Pending_check\n  Priority_rank"
    )
    chats = await db.get_pending_with_context(limit_per_chat=5)
    if not chats:
        await status_msg.edit_text(
            "Анализирую чаты\nTools:\n  Pending_check ✓ → 0 чатов\n\n"
            "Нет неотвеченных чатов! Все чисто."
        )
        return

    await status_msg.edit_text(
        f"Анализирую чаты\nTools:\n"
        f"  Pending_check ✓ → {len(chats)} чатов\n"
        f"  Priority_rank — анализ..."
    )
    ranking = await ai_client.rank_chat_priority(chats)
    await status_msg.edit_text(
        f"Анализирую чаты\nTools:\n"
        f"  Pending_check ✓ → {len(chats)} чатов\n"
        f"  Priority_rank ✓\n\n{ranking}"
    )


async def _handle_file_generate(
    msg, context: ContextTypes.DEFAULT_TYPE, chat_id: int
) -> None:
    """Generate a file (HTML, TXT, CSV, JSON, XML) from user request."""
    fmt = ai_client.detect_file_format(msg.text)
    status_msg = await msg.reply_text(
        f"Генерация файла\nTools:\n  File_generate → {fmt.upper()}"
    )
    await context.bot.send_chat_action(chat_id, "typing")

    content = await ai_client.generate_file_content(msg.text, fmt)

    await status_msg.edit_text(
        f"Генерация файла\nTools:\n  File_generate ✓ → {fmt.upper()}\n"
        f"  File_send"
    )

    suffix = f".{fmt}"
    filename = f"generated.{fmt}"
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, encoding="utf-8",
    ) as f:
        f.write(content)
        tmp_path = f.name

    try:
        await context.bot.send_chat_action(chat_id, "upload_document")
        with open(tmp_path, "rb") as doc:
            await msg.reply_document(
                document=doc,
                filename=filename,
                caption=f"Вот ваш {fmt.upper()} файл",
            )
        _add_to_history(chat_id, "assistant", f"Сгенерирован {fmt.upper()} файл")
        await status_msg.edit_text(
            f"Генерация файла\nTools:\n  File_generate ✓ → {fmt.upper()}\n"
            f"  File_send ✓\n\nФайл отправлен!"
        )
    except Exception as e:
        await status_msg.edit_text(
            f"Генерация файла\nTools:\n  File_generate ✓\n"
            f"  File_send — ошибка: {e}"
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


async def _handle_group_list_nl(msg, chat_id: int) -> None:
    """Show real group list from database when user asks in private chat."""
    status_msg = await msg.reply_text(
        "Проверяю группы\nTools:\n  Group_lookup"
    )
    groups = await db.get_known_group_chats()
    if not groups:
        answer = (
            "Я пока не добавлен ни в одну группу, либо ещё не видел "
            "сообщений в группах.\n\n"
            "Добавь меня в группу и напиши там хотя бы одно сообщение, "
            "тогда я запомню её."
        )
        _add_to_history(chat_id, "assistant", answer)
        await status_msg.edit_text(
            "Проверяю группы\nTools:\n  Group_lookup ✓ → 0 групп\n\n"
            + answer
        )
        return

    lines = []
    for i, g in enumerate(groups, 1):
        name = g.get("first_name") or "Группа"
        gid = g.get("chat_id") or ""
        lines.append(f"{i}. {name} (ID: {gid})")
    result = "\n".join(lines)
    answer = f"Я есть в {len(groups)} группах:\n{result}"
    _add_to_history(chat_id, "assistant", answer)
    await status_msg.edit_text(
        f"Проверяю группы\nTools:\n  Group_lookup ✓ → {len(groups)} групп\n\n"
        + answer
    )


_MODER_ACTION_RE = re.compile(
    r"(замуть|замути|мут(?:ни)?|заткни|забань|забани|бань|банни|бан(?=\s|$)|"
    r"кикни|кикнуть|кик(?=\s|$)|варни|предупреди|размуть|размути|разбань)",
    re.IGNORECASE,
)

_GROUP_NAME_RE = re.compile(
    r"(?:в\s+групп[уеа]\s+|в\s+)(.+?)(?:\s+(?:забань|забани|бань|бан|замуть|замути|мут|кикни|кик|варни|разбань|размуть|на\s+\d)|$)",
    re.IGNORECASE,
)


async def _handle_moderation_remote_nl(
    msg, context: ContextTypes.DEFAULT_TYPE, chat_id: int
) -> None:
    """Execute moderation actions (mute/ban/kick/warn) from private chat."""
    text = msg.text.strip()
    m_action = _MODER_ACTION_RE.search(text)
    if not m_action:
        await msg.reply_text("Не понял команду модерации.")
        return

    action_word = m_action.group(1).lower()
    if action_word in ("замуть", "замути", "заткни") or action_word.startswith("мут"):
        action = "mute"
    elif action_word in ("забань", "бань", "банни"):
        action = "ban"
    elif action_word in ("кикни", "кикнуть"):
        action = "kick"
    elif action_word in ("варни", "предупреди"):
        action = "warn"
    elif action_word in ("размуть", "размути"):
        action = "unmute"
    elif action_word == "разбань":
        action = "unban"
    else:
        await msg.reply_text("Не понял действие.")
        return

    action_labels = {
        "mute": "Mute_user", "ban": "Ban_user", "kick": "Kick_user",
        "warn": "Warn_user", "unmute": "Unmute_user", "unban": "Unban_user",
    }
    action_label = action_labels.get(action, action)

    target_username = _extract_username(text)
    if not target_username:
        await msg.reply_text(
            "Укажи @username пользователя.\n"
            "Пример: замуть @user на 10 минут"
        )
        return

    status_msg = await msg.reply_text(
        f"Выполняю модерацию\nTools:\n"
        f"  {action_label} → @{target_username}\n"
        f"  Group_lookup"
    )

    groups = await db.get_known_group_chats()
    if not groups:
        _add_to_history(chat_id, "assistant", "Не найдено групп для модерации.")
        await status_msg.edit_text(
            f"Выполняю модерацию\nTools:\n"
            f"  {action_label} → @{target_username}\n"
            f"  Group_lookup — нет групп\n\n"
            "Добавь бота в группу сначала."
        )
        return

    group_name_match = re.search(
        r"в\s+(?:групп[уеа]\s+)?[«\"]?(.+?)[»\"]?"
        r"(?:\s+(?:забань|забани|бань|бан\b|замуть|замути|мут|кикни|кик\b|варни|разбань|размуть)|$)",
        text, re.IGNORECASE,
    )
    group = groups[0]
    if group_name_match:
        requested_name = group_name_match.group(1).strip().lower()
        for g in groups:
            gname = (g.get("first_name") or "").lower()
            if requested_name in gname or gname in requested_name:
                group = g
                break
    group_id = group["chat_id"]
    group_name = group.get("first_name") or "Группа"

    period = _parse_period(text)
    reason_text = ""
    period_part = _PERIOD_RE.sub("", text) if period else text
    for w in (f"@{target_username}", action_word):
        period_part = period_part.replace(w, "")
    reason_text = re.sub(r"\s+", " ", period_part).strip()
    for noise in ("на", "в группу", "в группе", "минут", "час", "дня", "дней"):
        reason_text = reason_text.replace(noise, "").strip()
    reason_text = reason_text.strip(" ,.")

    target_user_id = None
    try:
        rows = await db.search_group_messages(group_id, f"@{target_username}", limit=1)
        if not rows:
            rows = await db.search_group_messages(group_id, target_username, limit=1)
        for row in rows:
            if row.get("username") and row["username"].lower() == target_username.lower():
                target_user_id = row["user_id"]
                break
            if target_user_id is None and row.get("user_id"):
                target_user_id = row["user_id"]
    except Exception:
        pass

    if not target_user_id:
        _add_to_history(
            chat_id, "assistant",
            f"Не найден @{target_username} в группе {group_name}."
        )
        await status_msg.edit_text(
            f"Выполняю модерацию\nTools:\n"
            f"  {action_label} → @{target_username}\n"
            f"  Group_lookup → {group_name} ✓\n"
            f"  User_lookup — @{target_username} не найден в группе\n\n"
            "Пользователь должен хотя бы раз написать в группе, "
            "чтобы бот его запомнил."
        )
        return

    await status_msg.edit_text(
        f"Выполняю модерацию\nTools:\n"
        f"  {action_label} → @{target_username}\n"
        f"  Group_lookup → {group_name} ✓\n"
        f"  User_lookup → ID {target_user_id} ✓\n"
        f"  {action_label}..."
    )

    from telegram import ChatPermissions

    try:
        if action == "mute":
            if not period:
                period = datetime.timedelta(weeks=1)
            until = datetime.datetime.now(datetime.timezone.utc) + period
            await context.bot.restrict_chat_member(
                chat_id=group_id,
                user_id=target_user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            result = f"🔇 @{target_username} замьючен на {_format_timedelta(period)} в {group_name}"

        elif action == "unmute":
            await context.bot.restrict_chat_member(
                chat_id=group_id,
                user_id=target_user_id,
                permissions=ChatPermissions(
                    can_send_messages=True,
                    can_send_media_messages=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                ),
            )
            result = f"🔊 @{target_username} размьючен в {group_name}"

        elif action == "ban":
            if period:
                until = datetime.datetime.now(datetime.timezone.utc) + period
                await context.bot.ban_chat_member(
                    chat_id=group_id, user_id=target_user_id, until_date=until
                )
                result = f"🚫 @{target_username} забанен на {_format_timedelta(period)} в {group_name}"
            else:
                await context.bot.ban_chat_member(
                    chat_id=group_id, user_id=target_user_id
                )
                result = f"🚫 @{target_username} забанен навсегда в {group_name}"

        elif action == "unban":
            await context.bot.unban_chat_member(
                chat_id=group_id, user_id=target_user_id
            )
            result = f"✅ @{target_username} разбанен в {group_name}"

        elif action == "kick":
            await context.bot.ban_chat_member(
                chat_id=group_id, user_id=target_user_id
            )
            await context.bot.unban_chat_member(
                chat_id=group_id, user_id=target_user_id
            )
            result = f"👢 @{target_username} кикнут из {group_name}"

        elif action == "warn":
            mod = await db.get_moderation(group_id)
            warn_limit = mod.get("warn_limit", 3) if mod else 3
            reason = reason_text or "Нет причины"
            expires_at = None
            if period:
                expires_at = (
                    datetime.datetime.utcnow() + period
                ).isoformat()
            count = await db.add_warning(
                group_id, target_user_id, reason, OWNER_ID, expires_at
            )
            result = (
                f"⚠️ @{target_username} получил предупреждение "
                f"({count}/{warn_limit}) в {group_name}"
            )
            if reason_text:
                result += f"\nПричина: {reason_text}"
            if count >= warn_limit:
                try:
                    await context.bot.ban_chat_member(
                        chat_id=group_id, user_id=target_user_id
                    )
                    await context.bot.unban_chat_member(
                        chat_id=group_id, user_id=target_user_id
                    )
                    await db.clear_warnings(group_id, target_user_id)
                    result += f"\n\n🚫 Кикнут за {warn_limit} предупреждений!"
                except Exception as e:
                    result += f"\n\nНе удалось кикнуть: {e}"
        else:
            result = "Неизвестное действие."

        if reason_text and action in ("mute", "ban"):
            result += f"\nПричина: {reason_text}"

        _add_to_history(chat_id, "assistant", result)
        await status_msg.edit_text(
            f"Выполняю модерацию\nTools:\n"
            f"  {action_label} → @{target_username}\n"
            f"  Group_lookup → {group_name} ✓\n"
            f"  User_lookup → ID {target_user_id} ✓\n"
            f"  {action_label} ✓\n\n"
            + result
        )
    except Exception as e:
        _add_to_history(chat_id, "assistant", f"Ошибка модерации: {e}")
        await status_msg.edit_text(
            f"Выполняю модерацию\nTools:\n"
            f"  {action_label} → @{target_username}\n"
            f"  Group_lookup → {group_name} ✓\n"
            f"  User_lookup → ID {target_user_id} ✓\n"
            f"  {action_label} — ошибка: {e}"
        )


# ── Group handlers ───────────────────────────────────────────────────


async def handle_group_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Track all messages in groups where the bot is a member."""
    msg = update.message
    if not msg:
        return

    if msg.chat.title:
        await db.upsert_known_group(msg.chat.id, msg.chat.title)

    blocked = await _check_moderation(update, context)
    if blocked:
        return

    user = msg.from_user
    media_type, file_id = _get_media_info(msg)

    await db.save_group_message(
        chat_id=msg.chat.id,
        message_id=msg.message_id,
        user_id=user.id if user else None,
        username=user.username if user else None,
        first_name=user.first_name if user else None,
        text=msg.text,
        media_type=media_type,
        file_id=file_id,
        caption=msg.caption,
        date=msg.date.isoformat() if msg.date else datetime.datetime.now().isoformat(),
    )


async def cmd_groupstats(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show message statistics for the current group."""
    if not await _check_group_access(update):
        return
    chat = update.effective_chat
    if not chat or chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах.")
        return

    hours = 24
    if context.args:
        try:
            hours = min(int(context.args[0]), 720)
        except ValueError:
            pass

    stats = await db.get_group_stats(chat.id, hours)

    if not stats:
        await update.message.reply_text(
            f"Нет сообщений за последние {hours} ч."
        )
        return

    total = sum(s["msg_count"] for s in stats)
    total_media = sum(s["media_count"] for s in stats)

    text = (
        f"<b>Статистика группы за {hours} ч.</b>\n\n"
        f"Всего сообщений: {total}\n"
        f"Медиа: {total_media}\n"
        f"Участников: {len(stats)}\n\n"
        f"<b>По участникам:</b>\n"
    )
    for i, s in enumerate(stats[:15], 1):
        name = s["first_name"] or "Неизвестный"
        username = f" @{s['username']}" if s.get("username") else ""
        text += f"{i}. {name}{username} — {s['msg_count']} сообщ."
        if s["media_count"]:
            text += f" ({s['media_count']} медиа)"
        text += "\n"

    await _safe_reply(update.message, text, parse_mode="HTML")


async def cmd_groupsummary(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """AI summary of recent messages in the group."""
    if not await _check_group_access(update):
        return
    chat = update.effective_chat
    if not chat or chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах.")
        return

    hours = 24
    if context.args:
        try:
            hours = min(int(context.args[0]), 168)
        except ValueError:
            pass

    status_msg = await update.message.reply_text(
        f"Анализирую чат за {hours} ч.\nTools:\n  Group_read\n  AI_summary"
    )

    messages = await db.get_group_recent_messages(chat.id, hours)

    if not messages:
        await status_msg.edit_text(
            f"Нет сообщений за последние {hours} ч."
        )
        return

    await status_msg.edit_text(
        f"Анализирую чат за {hours} ч.\nTools:\n"
        f"  Group_read ✓ → {len(messages)} сообщений\n"
        f"  AI_summary — генерирую..."
    )

    summary = await ai_client.summarize_messages(messages)

    await status_msg.edit_text(
        f"Анализирую чат за {hours} ч.\nTools:\n"
        f"  Group_read ✓ → {len(messages)} сообщений\n"
        f"  AI_summary ✓\n\n{summary}"
    )


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show top contributors in the group (all time)."""
    if not await _check_group_access(update):
        return
    chat = update.effective_chat
    if not chat or chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах.")
        return

    members = await db.get_group_top_members(chat.id, limit=10)

    if not members:
        await update.message.reply_text("Ещё нет данных о сообщениях в этой группе.")
        return

    text = "<b>Топ участников (за всё время):</b>\n\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, m in enumerate(members):
        prefix = medals[i] if i < 3 else f"{i + 1}."
        name = m["first_name"] or "Неизвестный"
        username = f" @{m['username']}" if m.get("username") else ""
        uid = m.get("user_id") or ""
        last_msg = (m.get("last_msg") or "")[:16].replace("T", " ")
        text += (
            f"{prefix} <b>{name}</b>{username}\n"
            f"    🆔 <code>{uid}</code>\n"
            f"    💬 {m['msg_count']} сообщ. | Последнее: {last_msg}\n\n"
        )

    await _safe_reply(update.message, text, parse_mode="HTML")


async def cmd_groupsearch(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Search messages in the current group."""
    if not await _check_group_access(update):
        return
    chat = update.effective_chat
    if not chat or chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах.")
        return

    if not context.args:
        await update.message.reply_text("Использование: /groupsearch [запрос]")
        return

    query = " ".join(context.args)
    results = await db.search_group_messages(chat.id, query)

    if not results:
        await update.message.reply_text(f'По запросу "{query}" ничего не найдено.')
        return

    text = f'<b>Результаты в группе по "{query}":</b>\n\n'
    for msg in results[:10]:
        name = msg.get("first_name") or "Неизвестный"
        username = f" @{msg['username']}" if msg.get("username") else ""
        content = msg.get("text") or msg.get("caption") or ""
        date_str = (msg.get("date") or "")[:16].replace("T", " ")
        text += f"👤 <b>{name}</b>{username}\n"
        text += f"📅 {date_str}\n"
        if content:
            text += f"{content[:200]}\n"
        text += "\n"

    await _safe_reply(update.message, text, parse_mode="HTML")


async def handle_group_question(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """AI Q&A in groups — responds when bot is mentioned or replied to."""
    msg = update.message
    if not msg or not msg.text:
        return

    if msg.text.startswith("/"):
        return

    bot_username = (context.bot.username or "").lower()
    is_reply_to_bot = (
        msg.reply_to_message
        and msg.reply_to_message.from_user
        and msg.reply_to_message.from_user.id == context.bot.id
    )
    is_mention = bot_username and f"@{bot_username}".lower() in msg.text.lower()

    if not is_reply_to_bot and not is_mention:
        return

    if not await _check_group_access(update):
        return

    chat_id = msg.chat.id
    user_text = msg.text.replace(f"@{bot_username}", "").strip() if bot_username else msg.text

    if not user_text:
        await msg.reply_text("Напиши вопрос после упоминания!")
        return

    _add_to_history(chat_id, "user", user_text)
    history = _get_history(chat_id)

    await context.bot.send_chat_action(chat_id, "typing")
    status_msg = await msg.reply_text("💭 Думаю...")

    response = await ai_client.answer_question(user_text, history=history[:-1], is_group=True)
    _add_to_history(chat_id, "assistant", response)

    try:
        await status_msg.edit_text(response)
    except Exception:
        await _safe_reply(msg, response)


# ── Photo handler ────────────────────────────────────────────────────


async def handle_photo_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Describe a photo sent by the user using AI vision."""
    msg = update.message
    if not msg:
        return
    if not await _check_auth(update):
        return

    photo = msg.photo[-1] if msg.photo else None
    if not photo:
        return

    chat_id = msg.chat.id
    caption = msg.caption or ""
    question = caption if caption else "Опиши это изображение подробно."

    status_msg = await msg.reply_text(
        "Анализирую фото\nTools:\n  Photo_download\n  Vision_AI"
    )

    try:
        tg_file = await context.bot.get_file(photo.file_id)
        local_path = os.path.join(
            tempfile.gettempdir(), f"photo_{msg.message_id}.jpg"
        )
        await tg_file.download_to_drive(local_path)
    except Exception as e:
        await status_msg.edit_text(
            f"Анализирую фото\nTools:\n"
            f"  Photo_download — ошибка: {e}"
        )
        return

    await status_msg.edit_text(
        "Анализирую фото\nTools:\n  Photo_download ✓\n  Vision_AI — анализ..."
    )

    description = await ai_client.describe_image(local_path, question)

    try:
        os.remove(local_path)
    except Exception:
        pass

    _add_to_history(chat_id, "user", f"[фото] {caption}")
    _add_to_history(chat_id, "assistant", description)

    await status_msg.edit_text(
        "Анализирую фото\nTools:\n  Photo_download ✓\n  Vision_AI ✓\n\n"
        f"{description}"
    )


# ── Whoami command ───────────────────────────────────────────────────


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the user their own Telegram info."""
    if not await _check_auth(update):
        return
    user = update.effective_user
    if not user:
        await update.message.reply_text("Не удалось определить пользователя.")
        return

    username = f"@{user.username}" if user.username else "не указан"
    lang = user.language_code or "не указан"
    is_owner = "Да" if user.id == OWNER_ID else "Нет"

    text = (
        f"<b>Твой профиль:</b>\n\n"
        f"👤 Имя: <b>{user.first_name or ''} {user.last_name or ''}</b>\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"📛 Username: {username}\n"
        f"🌐 Язык: {lang}\n"
        f"👑 Владелец бота: {is_owner}"
    )
    await _safe_reply(update.message, text, parse_mode="HTML")


# ── Access command ───────────────────────────────────────────────────


async def cmd_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set who can use the bot in group chats."""
    global _group_access_level

    user = update.effective_user
    if not user or user.id != OWNER_ID:
        await update.message.reply_text("Только владелец может менять доступ.")
        return

    if not context.args:
        levels = {
            GROUP_ACCESS_OWNER: "только владелец",
            GROUP_ACCESS_ADMINS: "владелец + админы",
            GROUP_ACCESS_ALL: "все участники",
        }
        current = levels.get(_group_access_level, _group_access_level)
        await update.message.reply_text(
            f"<b>Доступ в группах:</b>\n\n"
            f"Текущий: <b>{current}</b>\n\n"
            f"/access owner — только владелец\n"
            f"/access admins — владелец + админы\n"
            f"/access all — все участники",
            parse_mode="HTML",
        )
        return

    level = context.args[0].lower()
    if level in ("owner", "admins", "all"):
        _group_access_level = level
        labels = {
            "owner": "только владелец",
            "admins": "владелец + админы",
            "all": "все участники",
        }
        await update.message.reply_text(
            f"Доступ в группах: <b>{labels[level]}</b>",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            "Используй: /access owner | admins | all"
        )


# ── /chats and /groups commands ───────────────────────────────────────


async def cmd_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all known private chats the bot can send to."""
    if not await _check_auth(update):
        return
    chats = await db.get_known_chats()
    bc_chats = await db.get_all_business_chats()
    all_ids = {c["chat_id"] for c in chats}
    for c in bc_chats:
        if c["user_chat_id"] and c["user_chat_id"] not in all_ids:
            chats.append({"chat_id": c["user_chat_id"], "first_name": str(c.get("user_id", "")), "username": None, "user_id": c.get("user_id")})

    if not chats:
        await update.message.reply_text("Нет известных приватных чатов.")
        return

    text = "<b>Приватные чаты (ЛС):</b>\n\n"
    for i, c in enumerate(chats[:30], 1):
        name = c.get("first_name") or "Неизвестный"
        username = f" @{c['username']}" if c.get("username") else ""
        uid = c.get("user_id") or c.get("chat_id") or ""
        text += f"{i}. <b>{name}</b>{username}\n"
        text += f"   🆔 <code>{uid}</code>\n"
    text += f"\nВсего: {len(chats)}"
    await _safe_reply(update.message, text, parse_mode="HTML")


async def cmd_groups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all known groups the bot is in."""
    if not await _check_auth(update):
        return
    groups = await db.get_known_group_chats()
    if not groups:
        await update.message.reply_text(
            "Нет известных групп.\n"
            "Добавь бота в группу и напиши там хотя бы одно сообщение."
        )
        return

    text = "<b>Группы:</b>\n\n"
    for i, g in enumerate(groups, 1):
        name = g.get("first_name") or "Группа"
        gid = g.get("chat_id") or ""
        text += f"{i}. <b>{name}</b>\n"
        text += f"   🆔 <code>{gid}</code>\n"
    text += f"\nВсего: {len(groups)}"
    await _safe_reply(update.message, text, parse_mode="HTML")


# ── Moderation ───────────────────────────────────────────────────────


async def cmd_moder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle moderation mode for a group."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return
    if chat.type == "private":
        await update.message.reply_text("Модерация работает только в группах.")
        return
    if user.id != OWNER_ID:
        try:
            member = await chat.get_member(user.id)
            if member.status not in ("administrator", "creator"):
                await update.message.reply_text("Только админы могут управлять модерацией.")
                return
        except Exception:
            return

    if not context.args:
        mod = await db.get_moderation(chat.id)
        status = "включена" if (mod and mod["is_enabled"]) else "выключена"
        await update.message.reply_text(
            f"<b>Модерация:</b> {status}\n\n"
            f"/moder on — включить (работать)\n"
            f"/moder off — выключить (в сон)\n"
            f"/moder welcome [текст] — приветствие новых\n"
            f"/moder badwords [слова через запятую] — фильтр мата\n"
            f"/moder antiflood [макс] [сек] — антифлуд\n",
            parse_mode="HTML",
        )
        return

    action = context.args[0].lower()

    if action in ("on", "работай", "вкл"):
        await db.set_moderation(chat.id, is_enabled=1)
        await update.message.reply_text("Модерация включена.")
    elif action in ("off", "сон", "выкл"):
        await db.set_moderation(chat.id, is_enabled=0)
        await update.message.reply_text("Модерация выключена (в сон).")
    elif action == "welcome":
        text = " ".join(context.args[1:]) if len(context.args) > 1 else ""
        await db.set_moderation(chat.id, welcome_msg=text)
        if text:
            await update.message.reply_text(f"Приветствие установлено: {text}")
        else:
            await update.message.reply_text("Приветствие сброшено.")
    elif action == "badwords":
        words = " ".join(context.args[1:]) if len(context.args) > 1 else ""
        await db.set_moderation(chat.id, bad_words=words)
        if words:
            await update.message.reply_text(f"Запрещённые слова: {words}")
        else:
            await update.message.reply_text("Список запрещённых слов очищен.")
    elif action == "antiflood":
        max_msgs = 5
        seconds = 10
        if len(context.args) > 1:
            try:
                max_msgs = int(context.args[1])
            except ValueError:
                pass
        if len(context.args) > 2:
            try:
                seconds = int(context.args[2])
            except ValueError:
                pass
        await db.set_moderation(chat.id, antiflood_max=max_msgs, antiflood_seconds=seconds)
        await update.message.reply_text(f"Антифлуд: макс {max_msgs} сообщений за {seconds} сек.")
    else:
        await update.message.reply_text("Используй: /moder on | off | welcome | badwords | antiflood")


async def cmd_rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View or set group rules."""
    chat = update.effective_chat
    if not chat or chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах.")
        return

    if context.args:
        user = update.effective_user
        if not user or user.id != OWNER_ID:
            try:
                member = await chat.get_member(user.id)
                if member.status not in ("administrator", "creator"):
                    await update.message.reply_text("Только админы могут менять правила.")
                    return
            except Exception:
                return
        rules_text = " ".join(context.args)
        await db.set_moderation(chat.id, rules=rules_text)
        await update.message.reply_text("Правила обновлены!")
        return

    mod = await db.get_moderation(chat.id)
    rules = mod.get("rules") if mod else ""
    if rules:
        await _safe_reply(update.message, f"<b>Правила группы:</b>\n\n{rules}", parse_mode="HTML")
    else:
        await update.message.reply_text(
            "Правила не установлены.\n"
            "Используй: /rules [текст правил]"
        )


async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Warn a user (Iris-style). Supports period: /warn 2 дня reason."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    if not user or not await _check_admin(chat, user):
        await update.message.reply_text("Только админы могут выдавать предупреждения.")
        return

    reply = update.message.reply_to_message
    if not reply or not reply.from_user:
        await update.message.reply_text("Ответь на сообщение пользователя, которого хочешь предупредить.")
        return

    target = reply.from_user
    args_text = " ".join(context.args) if context.args else ""
    period = _parse_period(args_text)
    expires_at = None
    if period:
        expires_at = (datetime.datetime.utcnow() + period).isoformat()
        reason_text = _PERIOD_RE.sub("", args_text).strip() or "Нет причины"
    else:
        reason_text = args_text or "Нет причины"

    mod = await db.get_moderation(chat.id)
    warn_limit = mod.get("warn_limit", 3) if mod else 3
    count = await db.add_warning(chat.id, target.id, reason_text, user.id, expires_at)

    name = target.first_name or "Пользователь"
    period_str = f" на {_format_timedelta(period)}" if period else ""
    text = (
        f"⚠️ <b>{name}</b> получил предупреждение{period_str} ({count}/{warn_limit})\n"
        f"Причина: {reason_text}"
    )

    if count >= warn_limit:
        try:
            await chat.ban_member(target.id)
            await chat.unban_member(target.id)
            await db.clear_warnings(chat.id, target.id)
            text += f"\n\n🚫 {name} кикнут за {warn_limit} предупреждений!"
        except Exception as e:
            text += f"\n\nНе удалось кикнуть: {e}"

    await _safe_reply(update.message, text, parse_mode="HTML")


async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mute a user. Supports period: /mute 2 часа reason."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    if not user or not await _check_admin(chat, user):
        await update.message.reply_text("Только админы могут мутить.")
        return

    reply = update.message.reply_to_message
    if not reply or not reply.from_user:
        await update.message.reply_text("Ответь на сообщение пользователя для мута.")
        return

    target = reply.from_user
    args_text = " ".join(context.args) if context.args else ""
    period = _parse_period(args_text)
    if not period:
        period = datetime.timedelta(weeks=1)
    reason = _PERIOD_RE.sub("", args_text).strip()

    from telegram import ChatPermissions
    until = datetime.datetime.now(datetime.timezone.utc) + period
    try:
        await chat.restrict_member(
            target.id,
            ChatPermissions(can_send_messages=False),
            until_date=until,
        )
        name = target.first_name or "Пользователь"
        text = f"🔇 <b>{name}</b> замьючен на {_format_timedelta(period)}."
        if reason:
            text += f"\nПричина: {reason}"
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unmute a user in the group."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    if not user or user.id != OWNER_ID:
        try:
            member = await chat.get_member(user.id)
            if member.status not in ("administrator", "creator"):
                await update.message.reply_text("Только админы могут размьютить.")
                return
        except Exception:
            return

    reply = update.message.reply_to_message
    if not reply or not reply.from_user:
        await update.message.reply_text("Ответь на сообщение пользователя для размута.")
        return

    target = reply.from_user
    from telegram import ChatPermissions
    try:
        await chat.restrict_member(
            target.id,
            ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
        name = target.first_name or "Пользователь"
        await update.message.reply_text(
            f"🔊 <b>{name}</b> размьючен.",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kick a user from the group."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    if not user or user.id != OWNER_ID:
        try:
            member = await chat.get_member(user.id)
            if member.status not in ("administrator", "creator"):
                await update.message.reply_text("Только админы могут кикать.")
                return
        except Exception:
            return

    reply = update.message.reply_to_message
    if not reply or not reply.from_user:
        await update.message.reply_text("Ответь на сообщение пользователя для кика.")
        return

    target = reply.from_user
    try:
        await chat.ban_member(target.id)
        await chat.unban_member(target.id)
        name = target.first_name or "Пользователь"
        reason = " ".join(context.args) if context.args else ""
        text = f"🚫 <b>{name}</b> кикнут из группы."
        if reason:
            text += f"\nПричина: {reason}"
        await _safe_reply(update.message, text, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ban a user. Supports period: /ban 2 дня reason. Default=forever."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    if not user or not await _check_admin(chat, user):
        await update.message.reply_text("Только админы могут банить.")
        return

    reply = update.message.reply_to_message
    if not reply or not reply.from_user:
        await update.message.reply_text("Ответь на сообщение пользователя для бана.")
        return

    target = reply.from_user
    args_text = " ".join(context.args) if context.args else ""
    period = _parse_period(args_text)
    reason = _PERIOD_RE.sub("", args_text).strip()

    try:
        until_date = None
        if period:
            until_date = datetime.datetime.now(datetime.timezone.utc) + period
        await chat.ban_member(target.id, until_date=until_date)
        name = target.first_name or "Пользователь"
        period_str = f" на {_format_timedelta(period)}" if period else " навсегда"
        text = f"⛔ <b>{name}</b> забанен{period_str}."
        if reason:
            text += f"\nПричина: {reason}"
        await _safe_reply(update.message, text, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unban a user."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    if not user or user.id != OWNER_ID:
        try:
            member = await chat.get_member(user.id)
            if member.status not in ("administrator", "creator"):
                await update.message.reply_text("Только админы могут разбанивать.")
                return
        except Exception:
            return

    reply = update.message.reply_to_message
    if not reply or not reply.from_user:
        await update.message.reply_text("Ответь на сообщение пользователя.")
        return

    target = reply.from_user
    try:
        await chat.unban_member(target.id)
        name = target.first_name or "Пользователь"
        await update.message.reply_text(
            f"✅ <b>{name}</b> разбанен.",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


async def cmd_pin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pin a message in the group."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    if not user or user.id != OWNER_ID:
        try:
            member = await chat.get_member(user.id)
            if member.status not in ("administrator", "creator"):
                await update.message.reply_text("Только админы могут закреплять.")
                return
        except Exception:
            return

    reply = update.message.reply_to_message
    if not reply:
        await update.message.reply_text("Ответь на сообщение, которое нужно закрепить.")
        return

    try:
        await reply.pin()
        await update.message.reply_text("📌 Сообщение закреплено!")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


async def cmd_unpin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unpin a message in the group."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    if not user or user.id != OWNER_ID:
        try:
            member = await chat.get_member(user.id)
            if member.status not in ("administrator", "creator"):
                await update.message.reply_text("Только админы могут откреплять.")
                return
        except Exception:
            return

    reply = update.message.reply_to_message
    if reply:
        try:
            await reply.unpin()
            await update.message.reply_text("Сообщение откреплено.")
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")
    else:
        try:
            await chat.unpin_all_messages()
            await update.message.reply_text("Все сообщения откреплены.")
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")


async def cmd_poll(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create a poll. Usage: /poll Вопрос | вариант1 | вариант2 | ..."""
    if not await _check_group_access(update):
        return
    chat = update.effective_chat
    if not chat:
        return

    raw = " ".join(context.args) if context.args else ""
    if "|" not in raw:
        await update.message.reply_text(
            "Использование: /poll Вопрос | вариант1 | вариант2 | ...\n"
            "Минимум 2 варианта ответа."
        )
        return

    parts = [p.strip() for p in raw.split("|")]
    question = parts[0]
    options = [o for o in parts[1:] if o]

    if len(options) < 2:
        await update.message.reply_text("Нужно минимум 2 варианта ответа.")
        return
    if len(options) > 10:
        options = options[:10]

    try:
        await context.bot.send_poll(
            chat_id=chat.id,
            question=question,
            options=options,
            is_anonymous=False,
        )
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Report a message to admins."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах.")
        return

    reply = update.message.reply_to_message
    if not reply:
        await update.message.reply_text("Ответь на сообщение, которое хочешь пожаловаться.")
        return

    reporter = user.first_name if user else "Аноним"
    reported = reply.from_user
    reported_name = reported.first_name if reported else "Неизвестный"
    content = reply.text or reply.caption or "[медиа]"

    try:
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=(
                f"🚨 <b>Жалоба в {chat.title or 'группе'}</b>\n\n"
                f"От: {reporter}\n"
                f"На: {reported_name}\n"
                f"Сообщение: {content[:300]}\n"
                f"Группа ID: <code>{chat.id}</code>"
            ),
            parse_mode="HTML",
        )
        await update.message.reply_text("Жалоба отправлена админам.")
    except Exception:
        await update.message.reply_text("Не удалось отправить жалобу.")


async def handle_new_member(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Welcome new members if moderation is enabled."""
    msg = update.message
    if not msg or not msg.new_chat_members:
        return

    chat = update.effective_chat
    if not chat:
        return

    mod = await db.get_moderation(chat.id)
    if not mod or not mod.get("is_enabled"):
        return

    welcome = mod.get("welcome_msg", "")
    if not welcome:
        return

    for member in msg.new_chat_members:
        if member.id == context.bot.id:
            try:
                await msg.reply_text(
                    "Привет! Я gemeni — AI-ассистент.\n\n"
                    "⚠️ Чтобы я видел ВСЕ сообщения в группе "
                    "(включая Ирис-команды типа !варн, .бан), "
                    "отключите Group Privacy через @BotFather:\n"
                    "@BotFather → /mybots → выбери меня → "
                    "Bot Settings → Group Privacy → Turn off\n\n"
                    "Также сделайте меня админом для модерации.\n"
                    "Напишите /help для списка команд."
                )
            except Exception:
                pass
            continue
        if member.is_bot:
            continue
        name = member.first_name or "Новый участник"
        personal_welcome = welcome.replace("{name}", name)
        try:
            await msg.reply_text(personal_welcome)
        except Exception:
            pass


async def _check_moderation(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Check moderation rules (bad words, antiflood). Returns True if message was blocked."""
    msg = update.message
    if not msg:
        return False

    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user or chat.type == "private":
        return False

    if user.id == OWNER_ID:
        return False
    try:
        member = await chat.get_member(user.id)
        if member.status in ("administrator", "creator"):
            return False
    except Exception:
        pass

    mod = await db.get_moderation(chat.id)
    if not mod or not mod.get("is_enabled"):
        return False

    text_lower = (msg.text or "").lower()

    bad_words = mod.get("bad_words", "")
    if bad_words and text_lower:
        words = [w.strip().lower() for w in bad_words.split(",") if w.strip()]
        for w in words:
            if w in text_lower:
                try:
                    await msg.delete()
                    await context.bot.send_message(
                        chat_id=chat.id,
                        text=f"⚠️ {user.first_name}, запрещённое слово удалено.",
                    )
                except Exception:
                    pass
                return True

    max_msgs = mod.get("antiflood_max", 5)
    seconds = mod.get("antiflood_seconds", 10)
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(seconds=seconds)

    if chat.id not in _flood_tracker:
        _flood_tracker[chat.id] = {}
    user_times = _flood_tracker[chat.id].setdefault(user.id, [])
    user_times.append(now)
    user_times[:] = [t for t in user_times if t > cutoff]

    if len(user_times) > max_msgs:
        from telegram import ChatPermissions
        try:
            until = now + datetime.timedelta(minutes=5)
            await chat.restrict_member(
                user.id,
                ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            await context.bot.send_message(
                chat_id=chat.id,
                text=f"🔇 {user.first_name} замьючен на 5 мин (антифлуд).",
            )
        except Exception:
            pass
        _flood_tracker[chat.id][user.id] = []
        return True

    return False


# ── Iris-style commands (Russian text, no / prefix) ─────────────────

_IRIS_PREFIX_RE = re.compile(
    r"^(?:[!./]|ирис(?:ка)?\s+)",
    re.IGNORECASE,
)


def _strip_iris_prefix(text: str) -> str:
    return _IRIS_PREFIX_RE.sub("", text).strip()


_IRIS_COMMANDS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^варн(?:ы)?\s+лимит\s+(\d+)", re.I), "WARN_LIMIT"),
    (re.compile(r"^варн(?:ы|лист)\s*$", re.I), "WARNLIST"),
    (re.compile(r"^мои\s+варн", re.I), "MY_WARNS"),
    (re.compile(r"^варны\s+", re.I), "USER_WARNS"),
    (re.compile(r"^-варн", re.I), "UNWARN"),
    (re.compile(r"^снять\s+(?:все\s+)?варн", re.I), "CLEAR_WARNS"),
    (re.compile(r"^(?:варн|пред(?:упреждение)?)\s", re.I), "WARN"),
    (re.compile(r"^(?:мут|заткн(?:уть|и))\s", re.I), "MUTE"),
    (re.compile(r"^(?:-мут|размут|говори|unmute)", re.I), "UNMUTE"),
    (re.compile(r"^муты\s*$", re.I), "MUTELIST"),
    (re.compile(r"^(?:бан|чс)(?:\s|$)", re.I), "BAN"),
    (re.compile(r"^(?:-бан|разбан|unban)", re.I), "UNBAN"),
    (re.compile(r"^банлист\s*$", re.I), "BANLIST"),
    (re.compile(r"^кик(?:\s|$)", re.I), "KICK"),
    (re.compile(r"^кто\s+админ", re.I), "WHO_ADMIN"),
    (re.compile(r"^(?:а\s+судьи\s+кто|кто\s+здесь\s+власть)", re.I), "WHO_ADMIN"),
    (re.compile(r"^позвать\s+(?:админов|модеров)", re.I), "CALL_ADMINS"),
    (re.compile(r"^созвать\s+(?:модеров|админов)", re.I), "CALL_ADMINS"),
    (re.compile(r"^модер\s+лог", re.I), "MOD_LOG"),
]


async def handle_iris_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Iris-style Russian text commands in groups (!, ., Ирис, etc.)."""
    msg = update.message
    if not msg or not msg.text:
        return
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type == "private" or not user:
        return

    raw = msg.text.strip()
    if not _IRIS_PREFIX_RE.match(raw) and not any(
        p.search(raw) for p, _ in _IRIS_COMMANDS
    ):
        return

    body = _strip_iris_prefix(raw)
    if not body:
        return

    cmd_type = None
    match = None
    for pattern, ctype in _IRIS_COMMANDS:
        m = pattern.search(body)
        if m:
            cmd_type = ctype
            match = m
            break

    if not cmd_type:
        if _IRIS_PREFIX_RE.match(raw):
            await msg.reply_text(
                "Не понял команду. Доступные команды:\n"
                "варн, -варн, варнлист, мои варны\n"
                "мут, -мут, муты\n"
                "бан, -бан, банлист\n"
                "кик, кто админ, позвать админов\n\n"
                "Пример: варн спам, бан 2 дня реклама"
            )
        return

    reply = msg.reply_to_message
    target = reply.from_user if reply and reply.from_user else None

    if cmd_type == "WHO_ADMIN":
        await _iris_who_admin(msg, chat)
        return

    if cmd_type == "WARNLIST":
        await _iris_warnlist(msg, chat)
        return

    if cmd_type == "MY_WARNS":
        warns = await db.get_warnings(chat.id, user.id)
        if not warns:
            await msg.reply_text("У тебя нет предупреждений.")
        else:
            lines = [f"⚠️ Твои варны ({len(warns)}):"]
            for i, w in enumerate(warns, 1):
                reason = w.get("reason") or "—"
                lines.append(f"{i}. {reason}")
            await msg.reply_text("\n".join(lines))
        return

    if cmd_type == "USER_WARNS":
        if not target:
            await msg.reply_text("Ответь на сообщение пользователя.")
            return
        warns = await db.get_warnings(chat.id, target.id)
        name = target.first_name or "Пользователь"
        if not warns:
            await msg.reply_text(f"У {name} нет предупреждений.")
        else:
            lines = [f"⚠️ Варны {name} ({len(warns)}):"]
            for i, w in enumerate(warns, 1):
                reason = w.get("reason") or "—"
                lines.append(f"{i}. {reason}")
            await msg.reply_text("\n".join(lines))
        return

    if cmd_type == "WARN_LIMIT":
        if not await _check_admin(chat, user):
            await msg.reply_text("Только админы могут менять лимит варнов.")
            return
        new_limit = int(match.group(1))
        if new_limit < 1 or new_limit > 20:
            await msg.reply_text("Лимит должен быть от 1 до 20.")
            return
        await db.set_moderation(chat.id, warn_limit=new_limit)
        await msg.reply_text(f"Лимит предупреждений установлен: {new_limit}")
        return

    if not await _check_admin(chat, user):
        await msg.reply_text("Только админы могут использовать эту команду.")
        return

    if cmd_type == "WARN":
        if not target:
            await msg.reply_text("Ответь на сообщение пользователя для варна.")
            return
        rest = body[match.end():].strip()
        period = _parse_period(rest)
        expires_at = None
        if period:
            expires_at = (datetime.datetime.utcnow() + period).isoformat()
            reason = _PERIOD_RE.sub("", rest).strip() or "Нет причины"
        else:
            reason = rest or "Нет причины"
        mod = await db.get_moderation(chat.id)
        warn_limit = mod.get("warn_limit", 3) if mod else 3
        count = await db.add_warning(chat.id, target.id, reason, user.id, expires_at)
        name = target.first_name or "Пользователь"
        period_str = f" на {_format_timedelta(period)}" if period else ""
        text = (
            f"⚠️ <b>{name}</b> получил предупреждение{period_str} "
            f"({count}/{warn_limit})\nПричина: {reason}"
        )
        if count >= warn_limit:
            try:
                await chat.ban_member(target.id)
                await chat.unban_member(target.id)
                await db.clear_warnings(chat.id, target.id)
                text += f"\n\n🚫 {name} кикнут за {warn_limit} предупреждений!"
            except Exception as e:
                text += f"\n\nНе удалось кикнуть: {e}"
        await _safe_reply(msg, text, parse_mode="HTML")
        return

    if cmd_type == "UNWARN":
        if not target:
            await msg.reply_text("Ответь на сообщение пользователя для снятия варна.")
            return
        removed = await db.remove_last_warning(chat.id, target.id)
        name = target.first_name or "Пользователь"
        if removed:
            count = await db.get_active_warning_count(chat.id, target.id)
            await msg.reply_text(
                f"Последнее предупреждение <b>{name}</b> снято. Осталось: {count}",
                parse_mode="HTML",
            )
        else:
            await msg.reply_text(f"У {name} нет предупреждений.")
        return

    if cmd_type == "CLEAR_WARNS":
        if not target:
            await msg.reply_text("Ответь на сообщение пользователя.")
            return
        await db.clear_warnings(chat.id, target.id)
        name = target.first_name or "Пользователь"
        await msg.reply_text(
            f"Все предупреждения <b>{name}</b> сняты.", parse_mode="HTML"
        )
        return

    if cmd_type == "MUTE":
        if not target:
            await msg.reply_text("Ответь на сообщение пользователя для мута.")
            return
        rest = body[match.end():].strip()
        period = _parse_period(rest)
        if not period:
            period = datetime.timedelta(weeks=1)
        reason = _PERIOD_RE.sub("", rest).strip()
        from telegram import ChatPermissions
        until = datetime.datetime.now(datetime.timezone.utc) + period
        try:
            await chat.restrict_member(
                target.id,
                ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            name = target.first_name or "Пользователь"
            text = f"🔇 <b>{name}</b> замьючен на {_format_timedelta(period)}."
            if reason:
                text += f"\nПричина: {reason}"
            await msg.reply_text(text, parse_mode="HTML")
        except Exception as e:
            await msg.reply_text(f"Ошибка: {e}")
        return

    if cmd_type == "UNMUTE":
        if not target:
            await msg.reply_text("Ответь на сообщение пользователя для размута.")
            return
        from telegram import ChatPermissions
        try:
            await chat.restrict_member(
                target.id,
                ChatPermissions(
                    can_send_messages=True,
                    can_send_media_messages=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                ),
            )
            name = target.first_name or "Пользователь"
            await msg.reply_text(
                f"🔊 <b>{name}</b> размьючен.", parse_mode="HTML"
            )
        except Exception as e:
            await msg.reply_text(f"Ошибка: {e}")
        return

    if cmd_type == "MUTELIST":
        await msg.reply_text(
            "Список замьюченных можно проверить через настройки группы в Telegram."
        )
        return

    if cmd_type == "BAN":
        if not target:
            await msg.reply_text("Ответь на сообщение пользователя для бана.")
            return
        rest = body[match.end():].strip()
        period = _parse_period(rest)
        reason = _PERIOD_RE.sub("", rest).strip()
        try:
            until_date = None
            if period:
                until_date = datetime.datetime.now(datetime.timezone.utc) + period
            await chat.ban_member(target.id, until_date=until_date)
            name = target.first_name or "Пользователь"
            period_str = f" на {_format_timedelta(period)}" if period else " навсегда"
            text = f"⛔ <b>{name}</b> забанен{period_str}."
            if reason:
                text += f"\nПричина: {reason}"
            await _safe_reply(msg, text, parse_mode="HTML")
        except Exception as e:
            await msg.reply_text(f"Ошибка: {e}")
        return

    if cmd_type == "UNBAN":
        if not target:
            await msg.reply_text("Ответь на сообщение пользователя для разбана.")
            return
        try:
            await chat.unban_member(target.id)
            name = target.first_name or "Пользователь"
            await msg.reply_text(
                f"<b>{name}</b> разбанен.", parse_mode="HTML"
            )
        except Exception as e:
            await msg.reply_text(f"Ошибка: {e}")
        return

    if cmd_type == "BANLIST":
        await msg.reply_text(
            "Список заблокированных доступен в настройках группы → "
            "Управление группой → Заблокированные."
        )
        return

    if cmd_type == "KICK":
        if not target:
            await msg.reply_text("Ответь на сообщение пользователя для кика.")
            return
        try:
            await chat.ban_member(target.id)
            await chat.unban_member(target.id)
            name = target.first_name or "Пользователь"
            reason = body[match.end():].strip()
            text = f"🚫 <b>{name}</b> кикнут."
            if reason:
                text += f"\nПричина: {reason}"
            await _safe_reply(msg, text, parse_mode="HTML")
        except Exception as e:
            await msg.reply_text(f"Ошибка: {e}")
        return

    if cmd_type == "CALL_ADMINS":
        await _iris_call_admins(msg, chat, context)
        return

    if cmd_type == "MOD_LOG":
        warns = await db.get_recent_warnings_all(chat.id, 15)
        if not warns:
            await msg.reply_text("Лог модерации пуст.")
            return
        lines = ["📋 <b>Модер лог:</b>"]
        for w in warns:
            date_str = (w.get("created_at") or "")[:16]
            reason = w.get("reason") or "—"
            lines.append(f"• [{date_str}] user:{w['user_id']} → варн (ID {w['id']}): {reason}")
        await msg.reply_text("\n".join(lines[:20]), parse_mode="HTML")
        return


async def _iris_who_admin(msg, chat) -> None:
    lines = ["👑 <b>Состав модерации:</b>\n"]
    try:
        admins = await chat.get_administrators()
        for a in admins:
            name = a.user.first_name or "—"
            username = f" (@{a.user.username})" if a.user.username else ""
            role = "👤 Создатель" if a.status == "creator" else "🔧 Админ"
            lines.append(f"{role} {name}{username}")
    except Exception as e:
        lines.append(f"Ошибка: {e}")
    await msg.reply_text("\n".join(lines), parse_mode="HTML")


async def _iris_warnlist(msg, chat) -> None:
    warns = await db.get_recent_warnings_all(chat.id, 20)
    if not warns:
        await msg.reply_text("Варнлист пуст — предупреждений не было.")
        return
    lines = ["⚠️ <b>Последние предупреждения:</b>\n"]
    for w in warns:
        date_str = (w.get("created_at") or "")[:16]
        reason = w.get("reason") or "—"
        lines.append(f"• [{date_str}] user:{w['user_id']} — {reason}")
    await msg.reply_text("\n".join(lines), parse_mode="HTML")


async def _iris_call_admins(msg, chat, context) -> None:
    try:
        admins = await chat.get_administrators()
        mentions = []
        for a in admins:
            if not a.user.is_bot:
                name = a.user.first_name or "Админ"
                mentions.append(f'<a href="tg://user?id={a.user.id}">{name}</a>')
        if mentions:
            await msg.reply_text(
                f"🚨 <b>Созыв модерации!</b>\n\n{', '.join(mentions)}",
                parse_mode="HTML",
            )
        else:
            await msg.reply_text("Не удалось найти админов.")
    except Exception as e:
        await msg.reply_text(f"Ошибка: {e}")


async def cmd_warnlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show recent warnings in the chat."""
    chat = update.effective_chat
    if not chat or chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    await _iris_warnlist(update.message, chat)


async def cmd_whoadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show admin list (Iris-style 'Кто админ')."""
    chat = update.effective_chat
    if not chat or chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    await _iris_who_admin(update.message, chat)


async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove last warning from a user."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    if not user or not await _check_admin(chat, user):
        await update.message.reply_text("Только админы могут снимать варны.")
        return
    reply = update.message.reply_to_message
    if not reply or not reply.from_user:
        await update.message.reply_text("Ответь на сообщение пользователя.")
        return
    target = reply.from_user
    removed = await db.remove_last_warning(chat.id, target.id)
    name = target.first_name or "Пользователь"
    if removed:
        count = await db.get_active_warning_count(chat.id, target.id)
        await update.message.reply_text(
            f"Последнее предупреждение <b>{name}</b> снято. Осталось: {count}",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(f"У {name} нет предупреждений.")


async def cmd_warnlimit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set warn limit: /warnlimit 5."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    if not user or not await _check_admin(chat, user):
        await update.message.reply_text("Только админы могут менять лимит варнов.")
        return
    if not context.args:
        mod = await db.get_moderation(chat.id)
        cur = mod.get("warn_limit", 3) if mod else 3
        await update.message.reply_text(f"Текущий лимит предупреждений: {cur}")
        return
    try:
        new_limit = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Укажи число: /warnlimit 5")
        return
    if new_limit < 1 or new_limit > 20:
        await update.message.reply_text("Лимит должен быть от 1 до 20.")
        return
    await db.set_moderation(chat.id, warn_limit=new_limit)
    await update.message.reply_text(f"Лимит предупреждений: {new_limit}")


# ── Periodic jobs ────────────────────────────────────────────────────


async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    due = await db.get_due_reminders()
    for r in due:
        target = f" для {r['target_name']}" if r.get("target_name") else ""
        text = f"<b>Напоминание{target}!</b>\n\n{r['reminder_text']}"
        try:
            await context.bot.send_message(
                chat_id=r["owner_id"], text=text, parse_mode="HTML"
            )
        except Exception:
            pass


async def check_scheduled_messages(context: ContextTypes.DEFAULT_TYPE) -> None:
    due = await db.get_due_scheduled_messages()
    for s in due:
        try:
            if s.get("business_connection_id"):
                await context.bot.send_message(
                    chat_id=s["chat_id"],
                    text=s["text"],
                    business_connection_id=s["business_connection_id"],
                )
            else:
                await context.bot.send_message(
                    chat_id=s["chat_id"],
                    text=s["text"],
                )
            await context.bot.send_message(
                chat_id=s["owner_id"],
                text=f"<b>Запланированное сообщение отправлено!</b>\n\n{s['text']}",
                parse_mode="HTML",
            )
        except Exception:
            pass
