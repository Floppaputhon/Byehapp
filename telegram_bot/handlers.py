"""All bot handlers — business API events, commands, and periodic jobs."""

import datetime
import re

from telegram import Update
from telegram.ext import ContextTypes

import database as db
import ai_client
from config import OWNER_ID


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
        await message.reply_text(chunk, **kwargs)


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
        "- Статистика, поиск, перевод и анализ\n\n"
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
        "/help — справка\n\n"
        "Подключи меня как бизнес-бота в настройках Telegram!"
    )
    await _safe_reply(update.message, text, parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "<b>Справка по командам:</b>\n\n"
        "/deleted [кол-во] — показать удалённые сообщения\n"
        "  <i>Пример: /deleted 10</i>\n\n"
        "/summary [часы] — AI-пересказ за N часов\n"
        "  <i>Пример: /summary 12</i>\n\n"
        "/remind [кому] [что] [когда] — напоминание\n"
        "  <i>Пример: /remind @ivan позвонить 18:00</i>\n"
        "  <i>Пример: /remind мама перезвонить 30м</i>\n\n"
        "/pending — кому нужно ответить\n\n"
        "/stats [часы] — статистика за N часов\n"
        "  <i>Пример: /stats 48</i>\n\n"
        "/search [запрос] — поиск по сообщениям\n"
        "  <i>Пример: /search встреча завтра</i>\n\n"
        "/translate [текст] — перевод на русский\n"
        "  <i>Также можно ответить на сообщение</i>\n\n"
        "/analyze — анализ тона сообщения\n"
        "  <i>Ответь на сообщение этой командой</i>\n\n"
        "/myreminders — список активных напоминаний\n"
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
