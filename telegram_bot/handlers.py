"""All bot handlers — business API events, commands, and periodic jobs."""

import datetime
import re
from collections import deque

from telegram import Update
from telegram.ext import ContextTypes

import database as db
import ai_client
from config import OWNER_ID

_chat_history: dict[int, deque] = {}
_MAX_HISTORY = 20


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
            if bc_id:
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
    text = (
        "<b>Привет! Я твой бизнес-ассистент.</b>\n\n"
        "Что я умею:\n"
        "- Отслеживаю удалённые сообщения (текст, фото, видео)\n"
        "- Делаю AI-пересказы переписок\n"
        "- Напоминаю кому ответить/позвонить\n"
        "- Статистика, поиск, перевод и анализ\n"
        "- Автоответы, заметки, рассылка, шаблоны\n\n"
        "<b>Команды:</b>\n"
        "/deleted — удалённые сообщения\n"
        "/summary — AI-пересказ чата\n"
        "/remind — установить напоминание\n"
        "/pending — кому нужно ответить\n"
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
        "/help — справка\n\n"
        "Также можешь просто писать мне — я пойму!\n"
        "Подключи меня как бизнес-бота в настройках Telegram!"
    )
    await _safe_reply(update.message, text, parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "<b>Справка по командам:</b>\n\n"
        "/deleted [кол-во] — удалённые сообщения\n"
        "/summary [часы] — AI-пересказ за N часов\n"
        "/remind [кому] [что] [когда] — напоминание\n"
        "/pending — кому нужно ответить\n"
        "/stats [часы] — статистика\n"
        "/search [запрос] — поиск по сообщениям\n"
        "/translate [текст] — перевод\n"
        "/analyze — анализ тона сообщения\n"
        "/myreminders — мои напоминания\n\n"
        "<b>Новые инструменты:</b>\n\n"
        "/autoreply on [текст] — включить автоответ\n"
        "/autoreply off — выключить автоответ\n\n"
        "/note save [текст] — сохранить заметку\n"
        "/note list — список заметок\n"
        "/note delete [id] — удалить заметку\n"
        "/note search [запрос] — найти заметку\n\n"
        "/broadcast [текст] — рассылка всем контактам\n\n"
        "/contact @username — инфо о контакте\n\n"
        "/export [@username] — экспорт чата\n\n"
        "/qr save [имя] [текст] — шаблон ответа\n"
        "/qr list — список шаблонов\n"
        "/qr use [имя] — показать шаблон\n\n"
        "<b>Также можно писать обычным текстом:</b>\n"
        "• \"Запомни купить молоко\" → заметка\n"
        "• \"Напиши всем привет\" → рассылка\n"
        "• \"Расскажи про @ivan\" → инфо\n"
        "• \"Включи автоответ Я занят\" → автоответ\n"
    )
    await _safe_reply(update.message, text, parse_mode="HTML")


async def cmd_deleted(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        username = f" (@{msg['username']})" if msg.get("username") else ""
        content = msg.get("text") or msg.get("caption") or ""
        media = (
            f" [{MEDIA_LABELS.get(msg['media_type'], msg['media_type'])}]"
            if msg.get("media_type")
            else ""
        )
        date_str = (msg.get("deleted_at") or "")[:16].replace("T", " ")

        text += f"<b>{name}</b>{username}\n"
        text += f"Удалено: {date_str}\n"
        if content:
            text += f"{content[:200]}\n"
        if media:
            text += f"{media}\n"
        text += "\n"

    await _safe_reply(update.message, text, parse_mode="HTML")


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    pending = await db.get_pending_replies(limit=15)

    if not pending:
        await update.message.reply_text("Нет неотвеченных сообщений!")
        return

    text = "<b>Ожидают ответа:</b>\n\n"
    for msg in pending:
        name = msg["first_name"] or "Неизвестный"
        username = f" (@{msg['username']})" if msg.get("username") else ""
        content = (msg.get("text") or "")[:100]
        date_str = (msg.get("date") or "")[:16].replace("T", " ")

        text += f"<b>{name}</b>{username}\n"
        text += f"{date_str}\n"
        if content:
            text += f"{content}\n"
        text += "\n"

    await _safe_reply(update.message, text, parse_mode="HTML")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        await update.message.reply_text("Автоответ выключен.")
        return

    if action == "on":
        message = " ".join(context.args[1:]) if len(context.args) > 1 else "Я сейчас занят, отвечу позже."
        await db.set_autoreply(OWNER_ID, 1, message)
        await update.message.reply_text(
            f"<b>Автоответ включён!</b>\n\nСообщение: {message}",
            parse_mode="HTML",
        )
        return

    message = " ".join(context.args)
    await db.set_autoreply(OWNER_ID, 1, message)
    await update.message.reply_text(
        f"<b>Автоответ включён!</b>\n\nСообщение: {message}",
        parse_mode="HTML",
    )


# ── Notes command ────────────────────────────────────────────────────


async def cmd_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    text = (
        f"<b>Контакт: {name} (@{username})</b>\n\n"
        f"Всего сообщений: {stats['total_messages']}\n"
        f"Медиа: {stats['media_count']}\n"
        f"Удалённых: {stats['deleted_count']}\n"
        f"Первое сообщение: {first_seen}\n"
        f"Последнее: {last_seen}"
    )
    await _safe_reply(update.message, text, parse_mode="HTML")


# ── Export command ────────────────────────────────────────────────────


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    chat_id = msg.chat.id
    _add_to_history(chat_id, "user", msg.text)

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
    else:
        history = _get_history(chat_id)
        answer = await ai_client.answer_question(msg.text, history=history[:-1])
        _add_to_history(chat_id, "assistant", answer)
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

    target_username = _extract_username(msg.text)
    target_chat = None
    if target_username:
        target_chat = await db.find_chat_by_username(target_username)

    if not target_chat:
        chats = await db.get_known_chats()
        for c in chats:
            if c["chat_id"] != chat_id and c.get("user_id") != OWNER_ID:
                target_chat = c
                break

    if not target_chat:
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
        target_label = f"@{target_username}" if target_username else "получатель"
        _add_to_history(chat_id, "assistant", f"Не найден чат {target_label}. Текст: {composed}")
        await status_msg.edit_text(
            "Отправляю сообщение\n"
            "Tools:\n  Compose_message ✓\n"
            f"  Chat_lookup — не найден чат {target_label}\n\n"
            "Пользователь должен сначала написать тебе, чтобы бот увидел его через Business API."
        )
        return

    bc = await db.get_business_connection_for_chat(target_chat["chat_id"])
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

    send_chat_id = target_chat["chat_id"]
    chat_name = target_chat.get("first_name") or f"@{target_username or send_chat_id}"

    try:
        await context.bot.send_message(
            chat_id=send_chat_id,
            text=composed,
            business_connection_id=bc["connection_id"],
        )
        _add_to_history(chat_id, "assistant", f"Отправлено {chat_name}: {composed}")
        await status_msg.edit_text(
            "Отправляю сообщение\n"
            "Tools:\n  Compose_message ✓\n"
            f"  Chat_lookup → {chat_name} ✓\n"
            f"  Message_send ✓\n\n"
            f"Отправлено: {composed}"
        )
    except Exception as e:
        _add_to_history(chat_id, "assistant", f"Ошибка отправки: {e}")
        await status_msg.edit_text(
            "Отправляю сообщение\n"
            "Tools:\n  Compose_message ✓\n"
            f"  Chat_lookup → {chat_name} ✓\n"
            f"  Message_send — ошибка: {e}\n\n"
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

    target_username = _extract_username(msg.text)
    target_chat = None
    if target_username:
        target_chat = await db.find_chat_by_username(target_username)

    if not target_chat:
        chats = await db.get_known_chats()
        for c in chats:
            if c["chat_id"] != chat_id and c.get("user_id") != OWNER_ID:
                target_chat = c
                break

    if not target_chat:
        bc = await db.get_any_business_connection()
        if not bc:
            _add_to_history(chat_id, "assistant", f"Нет бизнес-подключения. Текст: {composed}")
            await status_msg.edit_text(
                "Планирую сообщение\n"
                "Tools:\n  Parse_time ✓\n"
                "  Compose_message ✓\n"
                "  Chat_lookup — нет бизнес-подключения\n\n"
                "Подключи бота как бизнес-бота в настройках Telegram."
            )
            return
        target_label = f"@{target_username}" if target_username else "получатель"
        _add_to_history(chat_id, "assistant", f"Не найден чат {target_label}. Текст: {composed}")
        await status_msg.edit_text(
            "Планирую сообщение\n"
            "Tools:\n  Parse_time ✓\n"
            "  Compose_message ✓\n"
            f"  Chat_lookup — не найден чат {target_label}\n\n"
            "Пользователь должен сначала написать тебе, чтобы бот увидел его через Business API."
        )
        return

    target_chat_id = target_chat["chat_id"]
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
    status_msg = await msg.reply_text(
        "Рассылка\nTools:\n  Compose_message"
    )

    composed = await ai_client.compose_message(msg.text)

    await status_msg.edit_text(
        "Рассылка\nTools:\n  Compose_message ✓\n  Broadcast_send"
    )

    chats = await db.get_all_business_chats()
    filtered = [c for c in chats if c.get("user_id") != OWNER_ID]

    if not filtered:
        _add_to_history(chat_id, "assistant", "Нет контактов для рассылки.")
        await status_msg.edit_text(
            "Рассылка\nTools:\n  Compose_message ✓\n"
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
        "Рассылка\nTools:\n  Compose_message ✓\n"
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

    if re.search(r"(?:выключи|убери|отключи|off|выкл)", text, re.IGNORECASE):
        await db.set_autoreply(OWNER_ID, 0, "")
        result = "Автоответ выключен."
        _add_to_history(chat_id, "assistant", result)
        await status_msg.edit_text(
            "Автоответ\nTools:\n  Autoreply_config ✓\n\n" + result
        )
        return

    ar_text = re.sub(
        r"^(?:включи|установи|поставь|установить)\s+автоответ\s*(?:на\s*)?",
        "", msg.text, flags=re.IGNORECASE,
    ).strip()
    if not ar_text or ar_text.lower() == msg.text.lower():
        ar_text = "Я сейчас занят, отвечу позже."

    await db.set_autoreply(OWNER_ID, 1, ar_text)
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
