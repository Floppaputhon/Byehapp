import logging
import re
import time

from telegram import Update
from telegram.ext import ContextTypes

from bot.database import (
    add_message,
    add_note,
    add_reminder,
    add_to_blacklist,
    authenticate_user,
    delete_template,
    get_active_business_connections,
    get_all_templates,
    get_authenticated_user_count,
    get_blacklist,
    get_business_connection_owner,
    get_chat_history,
    get_moderation_stats_today,
    get_notes,
    get_setting,
    get_template,
    is_admin,
    is_auto_reply_on,
    is_blacklisted,
    is_user_authenticated,
    log_moderation,
    remove_from_blacklist,
    save_business_connection,
    save_template,
    set_setting,
)
from bot.llm_client import (
    generate_reply,
    moderate_message,
    summarize_chat,
    transcribe_voice,
    translate_text,
)

logger = logging.getLogger(__name__)

BOT_START_TIME = time.time()


def _get_bot_name() -> str:
    return get_setting("bot_name") or "AI Assistant"


COMMANDS_HELP = (
    "!\u043e\u0442\u0432\u0435\u0442 \u0432\u043a\u043b/\u0432\u044b\u043a\u043b - \u0430\u0432\u0442\u043e-\u043e\u0442\u0432\u0435\u0442\u044b\n"
    "!\u0446\u0435\u043b\u044c all/admin/owner - \u043a\u043e\u043c\u0443 \u043e\u0442\u0432\u0435\u0447\u0430\u0442\u044c\n"
    "!\u0438\u043c\u044f <\u0438\u043c\u044f> - \u0438\u043c\u044f \u0431\u043e\u0442\u0430\n"
    "!\u0431\u0430\u043d / !\u0440\u0430\u0437\u0431\u0430\u043d - \u0447\u0435\u0440\u043d\u044b\u0439 \u0441\u043f\u0438\u0441\u043e\u043a\n"
    "!\u0448\u0430\u0431\u043b\u043e\u043d - \u0431\u044b\u0441\u0442\u0440\u044b\u0435 \u0448\u0430\u0431\u043b\u043e\u043d\u044b\n"
    "!\u0437\u0430\u043c\u0435\u0442\u043a\u0430 @user \u0442\u0435\u043a\u0441\u0442 - \u0437\u0430\u043c\u0435\u0442\u043a\u0438 \u043e \u043a\u043b\u0438\u0435\u043d\u0442\u0435\n"
    "!\u043f\u0435\u0440\u0435\u0432\u043e\u0434 en \u0442\u0435\u043a\u0441\u0442 - \u043f\u0435\u0440\u0435\u0432\u043e\u0434\n"
    "!\u0440\u0435\u0437\u044e\u043c\u0435 - \u0440\u0435\u0437\u044e\u043c\u0435 \u0447\u0430\u0442\u0430\n"
    "!\u043d\u0430\u043f\u043e\u043c\u043d\u0438 5\u043c \u0442\u0435\u043a\u0441\u0442 - \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435\n"
    "!status - \u043f\u0430\u043d\u0435\u043b\u044c \u0441\u0442\u0430\u0442\u0443\u0441\u0430"
)


async def start_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.effective_message is None or update.effective_user is None:
        return

    user_id = update.effective_user.id

    if is_user_authenticated(user_id):
        bot_name = _get_bot_name()
        await update.effective_message.reply_text(
            f"\u0412\u044b \u0443\u0436\u0435 \u0430\u0432\u0442\u043e\u0440\u0438\u0437\u043e\u0432\u0430\u043d\u044b! {bot_name} \u0433\u043e\u0442\u043e\u0432 \u043a \u0440\u0430\u0431\u043e\u0442\u0435.\n\n"
            f"\u041a\u043e\u043c\u0430\u043d\u0434\u044b:\n{COMMANDS_HELP}"
        )
        return

    await update.effective_message.reply_text(
        "\u0414\u043e\u0431\u0440\u043e \u043f\u043e\u0436\u0430\u043b\u043e\u0432\u0430\u0442\u044c! \u0414\u043b\u044f \u0434\u043e\u0441\u0442\u0443\u043f\u0430 \u043a \u0431\u043e\u0442\u0443 \u0432\u0432\u0435\u0434\u0438\u0442\u0435 \u043f\u0430\u0440\u043e\u043b\u044c:"
    )


async def handle_business_connection(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    connection = update.business_connection
    if connection is None:
        return

    user = connection.user
    owner_id = user.id
    owner_username = user.username

    save_business_connection(
        connection_id=connection.id,
        owner_id=owner_id,
        owner_username=owner_username,
        can_reply=connection.can_reply,
        is_enabled=connection.is_enabled,
    )

    status = "\u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0435\u043d" if connection.is_enabled else "\u043e\u0442\u043a\u043b\u044e\u0447\u0435\u043d"
    logger.info(
        "Business connection %s: user %d (@%s), status=%s, can_reply=%s",
        connection.id, owner_id, owner_username, status, connection.can_reply,
    )

    if connection.is_enabled:
        try:
            can_reply_str = "\u0434\u0430" if connection.can_reply else "\u043d\u0435\u0442"
            await context.bot.send_message(
                chat_id=connection.user_chat_id,
                text=(
                    "\u2705 \u0411\u0438\u0437\u043d\u0435\u0441-\u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u0435 \u0430\u043a\u0442\u0438\u0432\u0438\u0440\u043e\u0432\u0430\u043d\u043e!\n\n"
                    f"\u041c\u043e\u0433\u0443 \u043e\u0442\u0432\u0435\u0447\u0430\u0442\u044c \u043e\u0442 \u0432\u0430\u0448\u0435\u0433\u043e \u0438\u043c\u0435\u043d\u0438: {can_reply_str}\n\n"
                    "\u0422\u0435\u043f\u0435\u0440\u044c \u044f \u0431\u0443\u0434\u0443:\n"
                    "\u2022 \u041c\u043e\u0434\u0435\u0440\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0432\u0445\u043e\u0434\u044f\u0449\u0438\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u044f\n"
                    "\u2022 \u041e\u0442\u0432\u0435\u0447\u0430\u0442\u044c \u043a\u043b\u0438\u0435\u043d\u0442\u0430\u043c \u043e\u0442 \u0432\u0430\u0448\u0435\u0433\u043e \u0438\u043c\u0435\u043d\u0438 "
                    "(\u0435\u0441\u043b\u0438 \u0430\u0432\u0442\u043e-\u043e\u0442\u0432\u0435\u0442\u044b \u0432\u043a\u043b\u044e\u0447\u0435\u043d\u044b)\n\n"
                    "\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435 !\u043e\u0442\u0432\u0435\u0442 \u0432\u043a\u043b \u0447\u0442\u043e\u0431\u044b \u0432\u043a\u043b\u044e\u0447\u0438\u0442\u044c \u0430\u0432\u0442\u043e-\u043e\u0442\u0432\u0435\u0442\u044b."
                ),
            )
        except Exception as e:
            logger.warning("Could not send business notification: %s", e)


async def handle_voice_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    if message is None or update.effective_user is None:
        return

    user_id = update.effective_user.id
    if not is_user_authenticated(user_id):
        await message.reply_text("\u0414\u043b\u044f \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u043d\u0438\u044f \u0431\u043e\u0442\u0430 \u0432\u0432\u0435\u0434\u0438\u0442\u0435 \u043f\u0430\u0440\u043e\u043b\u044c.")
        return

    voice = message.voice or message.audio
    if voice is None:
        return

    await message.reply_text("\u0420\u0430\u0441\u0448\u0438\u0444\u0440\u043e\u0432\u044b\u0432\u0430\u044e \u0433\u043e\u043b\u043e\u0441\u043e\u0432\u043e\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435...")

    voice_file = await context.bot.get_file(voice.file_id)
    file_bytes = await voice_file.download_as_bytearray()

    transcript = await transcribe_voice(bytes(file_bytes))
    if not transcript:
        await message.reply_text(
            "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0440\u0430\u0441\u0448\u0438\u0444\u0440\u043e\u0432\u0430\u0442\u044c \u0433\u043e\u043b\u043e\u0441\u043e\u0432\u043e\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435."
        )
        return

    chat_id = message.chat_id
    add_message(chat_id, user_id, "user", transcript)

    reply = f"\u0420\u0430\u0441\u0448\u0438\u0444\u0440\u043e\u0432\u043a\u0430:\n{transcript}"

    if is_auto_reply_on():
        history = get_chat_history(chat_id)
        ai_reply = await generate_reply(
            history, transcript, bot_name=_get_bot_name(),
        )
        if ai_reply:
            add_message(chat_id, 0, "assistant", ai_reply)
            reply += f"\n\n\u041e\u0442\u0432\u0435\u0442:\n{ai_reply}"

    await message.reply_text(reply)


async def handle_business_voice(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.business_message is None:
        return

    message = update.business_message
    voice = message.voice or message.audio
    if voice is None:
        return

    user_id = message.from_user.id if message.from_user else 0
    chat_id = message.chat.id
    connection_id = message.business_connection_id

    owner_id = (
        get_business_connection_owner(connection_id)
        if connection_id else None
    )
    if owner_id is not None and user_id == owner_id:
        return

    if is_blacklisted(user_id):
        return

    voice_file = await context.bot.get_file(voice.file_id)
    file_bytes = await voice_file.download_as_bytearray()

    transcript = await transcribe_voice(bytes(file_bytes))
    if not transcript:
        return

    add_message(chat_id, user_id, "user", transcript)

    reply_target = get_setting("reply_target") or "all"
    if not _should_reply(reply_target, user_id, owner_id):
        return

    if is_auto_reply_on():
        history = get_chat_history(chat_id)
        ai_reply = await generate_reply(
            history, transcript, bot_name=_get_bot_name(),
        )
        if ai_reply:
            full_reply = (
                f"\u0420\u0430\u0441\u0448\u0438\u0444\u0440\u043e\u0432\u043a\u0430:\n{transcript}\n\n\u041e\u0442\u0432\u0435\u0442:\n{ai_reply}"
            )
            add_message(chat_id, 0, "assistant", ai_reply)
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=full_reply,
                    business_connection_id=connection_id,
                )
            except Exception as e:
                logger.warning("Could not reply to business voice: %s", e)


async def handle_direct_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.effective_message is None or update.effective_user is None:
        return

    message = update.effective_message
    user_id = update.effective_user.id
    text = (message.text or "").strip()

    if not text:
        return

    if not is_user_authenticated(user_id):
        await _handle_auth(update, text)
        return

    lower = text.lower()

    if lower.startswith("!\u043e\u0442\u0432\u0435\u0442"):
        await _handle_auto_reply_command(update, text)
        return
    if lower == "!status":
        await _handle_status_command(update)
        return
    if lower.startswith("!\u0446\u0435\u043b\u044c"):
        await _handle_reply_target_command(update, text)
        return
    if lower.startswith("!\u0438\u043c\u044f"):
        await _handle_name_command(update, text)
        return
    if lower.startswith("!\u0431\u0430\u043d"):
        await _handle_ban_command(update, text)
        return
    if lower.startswith("!\u0440\u0430\u0437\u0431\u0430\u043d"):
        await _handle_unban_command(update, text)
        return
    if lower.startswith("!\u0448\u0430\u0431\u043b\u043e\u043d"):
        await _handle_template_command(update, text)
        return
    if lower.startswith("!\u0437\u0430\u043c\u0435\u0442\u043a\u0430"):
        await _handle_note_command(update, text)
        return
    if lower.startswith("!\u043f\u0435\u0440\u0435\u0432\u043e\u0434"):
        await _handle_translate_command(update, text)
        return
    if lower == "!\u0440\u0435\u0437\u044e\u043c\u0435":
        await _handle_summary_command(update)
        return
    if lower.startswith("!\u043d\u0430\u043f\u043e\u043c\u043d\u0438"):
        await _handle_reminder_command(update, text, context)
        return

    chat_id = message.chat_id
    add_message(chat_id, user_id, "user", text)

    history = get_chat_history(chat_id)
    reply_text = await generate_reply(
        history, text, bot_name=_get_bot_name(),
    )

    if reply_text:
        add_message(chat_id, 0, "assistant", reply_text)
        await message.reply_text(reply_text)


async def handle_group_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.effective_message is None or update.effective_user is None:
        return

    message = update.effective_message
    user_id = update.effective_user.id
    text = (message.text or "").strip()

    if not text:
        return

    if is_blacklisted(user_id):
        return

    bot_me = await context.bot.get_me()
    bot_username = bot_me.username or ""
    bot_id = bot_me.id

    is_reply_to_bot = (
        message.reply_to_message is not None
        and message.reply_to_message.from_user is not None
        and message.reply_to_message.from_user.id == bot_id
    )
    is_mentioned = bot_username and f"@{bot_username}" in text

    if not is_reply_to_bot and not is_mentioned:
        return

    clean_text = (
        text.replace(f"@{bot_username}", "").strip()
        if is_mentioned else text
    )
    if not clean_text:
        clean_text = "\u041f\u0440\u0438\u0432\u0435\u0442!"

    chat_id = message.chat_id
    add_message(chat_id, user_id, "user", clean_text)

    history = get_chat_history(chat_id)
    reply_text = await generate_reply(
        history, clean_text, bot_name=_get_bot_name(), is_group=True,
    )

    if reply_text:
        add_message(chat_id, 0, "assistant", reply_text)
        await message.reply_text(reply_text)


async def handle_business_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.business_message is None:
        return

    message = update.business_message
    user_id = message.from_user.id if message.from_user else 0
    chat_id = message.chat.id
    text = (message.text or "").strip()
    connection_id = message.business_connection_id

    if not text:
        return

    owner_id = (
        get_business_connection_owner(connection_id)
        if connection_id else None
    )
    if owner_id is not None and user_id == owner_id:
        add_message(chat_id, user_id, "assistant", text)
        return

    if is_blacklisted(user_id):
        logger.info("Ignored blacklisted user %d", user_id)
        return

    reply_target = get_setting("reply_target") or "all"

    moderation_result = await moderate_message(text)
    log_moderation(
        chat_id=chat_id,
        user_id=user_id,
        message_text=text,
        action="blocked" if moderation_result["block"] else "passed",
        reason=str(moderation_result.get("reason", "")),
    )

    if moderation_result["block"]:
        logger.info(
            "Blocked message from user %d in chat %d: %s",
            user_id, chat_id, moderation_result.get("reason", ""),
        )
        try:
            await message.delete()
        except Exception as e:
            logger.warning("Could not delete message: %s", e)
        return

    add_message(chat_id, user_id, "user", text)

    if is_auto_reply_on() and _should_reply(reply_target, user_id, owner_id):
        history = get_chat_history(chat_id)
        reply_text = await generate_reply(
            history, text, bot_name=_get_bot_name(),
        )
        if reply_text:
            add_message(chat_id, 0, "assistant", reply_text)
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=reply_text,
                    business_connection_id=connection_id,
                )
            except Exception as e:
                logger.warning("Could not reply to business msg: %s", e)


async def _handle_auth(update: Update, text: str) -> None:
    from bot.config import AUTH_PASSWORD

    if update.effective_message is None or update.effective_user is None:
        return

    user_id = update.effective_user.id
    username = update.effective_user.username

    if text == AUTH_PASSWORD:
        authenticate_user(user_id, username, as_admin=True)
        await update.effective_message.reply_text(
            "\u041f\u0430\u0440\u043e\u043b\u044c \u0432\u0435\u0440\u043d\u044b\u0439! \u0412\u044b \u0430\u0432\u0442\u043e\u0440\u0438\u0437\u043e\u0432\u0430\u043d\u044b \u043a\u0430\u043a \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440.\n\n"
            f"\u041a\u043e\u043c\u0430\u043d\u0434\u044b:\n{COMMANDS_HELP}"
        )
    else:
        await update.effective_message.reply_text(
            "\u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u043f\u0430\u0440\u043e\u043b\u044c. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u0435\u0449\u0451 \u0440\u0430\u0437."
        )


def _should_reply(
    reply_target: str, user_id: int, owner_id: int | None,
) -> bool:
    if reply_target == "all":
        return True
    if reply_target == "owner":
        return owner_id is not None and user_id == owner_id
    if reply_target == "admin":
        return is_admin(user_id) or (
            owner_id is not None and user_id == owner_id
        )
    return True


async def _handle_reply_target_command(update: Update, text: str) -> None:
    if update.effective_message is None or update.effective_user is None:
        return

    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.effective_message.reply_text(
            "\u042d\u0442\u0430 \u043a\u043e\u043c\u0430\u043d\u0434\u0430 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430 \u0442\u043e\u043b\u044c\u043a\u043e \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440\u0430\u043c."
        )
        return

    parts = text.lower().split()
    valid_targets = {"all": "\u0432\u0441\u0435\u043c", "admin": "\u0430\u0434\u043c\u0438\u043d\u0430\u043c", "owner": "\u0432\u043b\u0430\u0434\u0435\u043b\u044c\u0446\u0443"}

    if len(parts) >= 2:
        target = parts[1]
        if target in valid_targets:
            set_setting("reply_target", target)
            await update.effective_message.reply_text(
                f"\u0426\u0435\u043b\u044c \u043e\u0442\u0432\u0435\u0442\u043e\u0432: {valid_targets[target]}"
            )
            return

    current = get_setting("reply_target") or "all"
    await update.effective_message.reply_text(
        f"\u0422\u0435\u043a\u0443\u0449\u0430\u044f \u0446\u0435\u043b\u044c: {valid_targets.get(current, current)}\n\n"
        "\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435:\n"
        "!\u0446\u0435\u043b\u044c all - \u043e\u0442\u0432\u0435\u0447\u0430\u0442\u044c \u0432\u0441\u0435\u043c\n"
        "!\u0446\u0435\u043b\u044c admin - \u0442\u043e\u043b\u044c\u043a\u043e \u0430\u0434\u043c\u0438\u043d\u0430\u043c \u0438 \u0432\u043b\u0430\u0434\u0435\u043b\u044c\u0446\u0443\n"
        "!\u0446\u0435\u043b\u044c owner - \u0442\u043e\u043b\u044c\u043a\u043e \u0432\u043b\u0430\u0434\u0435\u043b\u044c\u0446\u0443 \u0431\u043e\u0442\u0430"
    )


async def _handle_auto_reply_command(update: Update, text: str) -> None:
    if update.effective_message is None or update.effective_user is None:
        return

    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.effective_message.reply_text(
            "\u042d\u0442\u0430 \u043a\u043e\u043c\u0430\u043d\u0434\u0430 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430 \u0442\u043e\u043b\u044c\u043a\u043e \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440\u0430\u043c."
        )
        return

    parts = text.lower().split()
    if len(parts) >= 2:
        sub = parts[1]
        if sub == "\u0432\u043a\u043b":
            set_setting("auto_reply", "on")
            await update.effective_message.reply_text(
                "\u0410\u0432\u0442\u043e-\u043e\u0442\u0432\u0435\u0442\u044b \u0412\u041a\u041b\u042e\u0427\u0415\u041d\u042b."
            )
            return
        if sub == "\u0432\u044b\u043a\u043b":
            set_setting("auto_reply", "off")
            await update.effective_message.reply_text(
                "\u0410\u0432\u0442\u043e-\u043e\u0442\u0432\u0435\u0442\u044b \u0412\u042b\u041a\u041b\u042e\u0427\u0415\u041d\u042b."
            )
            return

    status = "\u0412\u041a\u041b" if is_auto_reply_on() else "\u0412\u042b\u041a\u041b"
    await update.effective_message.reply_text(
        f"\u0422\u0435\u043a\u0443\u0449\u0438\u0439 \u0441\u0442\u0430\u0442\u0443\u0441 \u0430\u0432\u0442\u043e-\u043e\u0442\u0432\u0435\u0442\u043e\u0432: {status}\n\n"
        "\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435:\n"
        "!\u043e\u0442\u0432\u0435\u0442 \u0432\u043a\u043b - \u0432\u043a\u043b\u044e\u0447\u0438\u0442\u044c\n"
        "!\u043e\u0442\u0432\u0435\u0442 \u0432\u044b\u043a\u043b - \u0432\u044b\u043a\u043b\u044e\u0447\u0438\u0442\u044c"
    )


async def _handle_name_command(update: Update, text: str) -> None:
    if update.effective_message is None or update.effective_user is None:
        return

    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.effective_message.reply_text(
            "\u042d\u0442\u0430 \u043a\u043e\u043c\u0430\u043d\u0434\u0430 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430 \u0442\u043e\u043b\u044c\u043a\u043e \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440\u0430\u043c."
        )
        return

    parts = text.split(maxsplit=1)
    if len(parts) >= 2:
        new_name = parts[1].strip()
        if new_name:
            set_setting("bot_name", new_name)
            await update.effective_message.reply_text(
                f"\u0418\u043c\u044f \u0431\u043e\u0442\u0430 \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u043e \u043d\u0430: {new_name}"
            )
            return

    current = _get_bot_name()
    await update.effective_message.reply_text(
        f"\u0422\u0435\u043a\u0443\u0449\u0435\u0435 \u0438\u043c\u044f: {current}\n\n"
        "\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435: !\u0438\u043c\u044f <\u043d\u043e\u0432\u043e\u0435 \u0438\u043c\u044f>"
    )


async def _handle_ban_command(update: Update, text: str) -> None:
    if update.effective_message is None or update.effective_user is None:
        return

    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.effective_message.reply_text(
            "\u042d\u0442\u0430 \u043a\u043e\u043c\u0430\u043d\u0434\u0430 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430 \u0442\u043e\u043b\u044c\u043a\u043e \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440\u0430\u043c."
        )
        return

    parts = text.split(maxsplit=2)
    if len(parts) >= 2:
        target = parts[1].strip()
        reason = parts[2].strip() if len(parts) > 2 else ""
        try:
            target_id = int(target)
            add_to_blacklist(target_id, None, reason)
            await update.effective_message.reply_text(
                f"\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c {target_id} \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d \u0432 \u0447\u0435\u0440\u043d\u044b\u0439 \u0441\u043f\u0438\u0441\u043e\u043a."
            )
            return
        except ValueError:
            pass

    bl = get_blacklist()
    if bl:
        lines = ["\u0427\u0435\u0440\u043d\u044b\u0439 \u0441\u043f\u0438\u0441\u043e\u043a:"]
        for item in bl:
            name = (
                f"@{item['username']}" if item["username"]
                else str(item["user_id"])
            )
            suffix = f" ({item['reason']})" if item["reason"] else ""
            lines.append(f"  - {name}{suffix}")
        await update.effective_message.reply_text("\n".join(lines))
    else:
        await update.effective_message.reply_text(
            "\u0427\u0435\u0440\u043d\u044b\u0439 \u0441\u043f\u0438\u0441\u043e\u043a \u043f\u0443\u0441\u0442.\n\n"
            "\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435: !\u0431\u0430\u043d <user_id> [\u043f\u0440\u0438\u0447\u0438\u043d\u0430]"
        )


async def _handle_unban_command(update: Update, text: str) -> None:
    if update.effective_message is None or update.effective_user is None:
        return

    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.effective_message.reply_text(
            "\u042d\u0442\u0430 \u043a\u043e\u043c\u0430\u043d\u0434\u0430 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430 \u0442\u043e\u043b\u044c\u043a\u043e \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440\u0430\u043c."
        )
        return

    parts = text.split(maxsplit=1)
    if len(parts) >= 2:
        try:
            target_id = int(parts[1].strip())
            if remove_from_blacklist(target_id):
                await update.effective_message.reply_text(
                    f"\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c {target_id} \u0443\u0434\u0430\u043b\u0435\u043d \u0438\u0437 \u0447\u0435\u0440\u043d\u043e\u0433\u043e \u0441\u043f\u0438\u0441\u043a\u0430."
                )
            else:
                await update.effective_message.reply_text(
                    "\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d \u0432 \u0447\u0435\u0440\u043d\u043e\u043c \u0441\u043f\u0438\u0441\u043a\u0435."
                )
            return
        except ValueError:
            pass

    await update.effective_message.reply_text(
        "\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435: !\u0440\u0430\u0437\u0431\u0430\u043d <user_id>"
    )


async def _handle_template_command(update: Update, text: str) -> None:
    if update.effective_message is None or update.effective_user is None:
        return

    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.effective_message.reply_text(
            "\u042d\u0442\u0430 \u043a\u043e\u043c\u0430\u043d\u0434\u0430 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430 \u0442\u043e\u043b\u044c\u043a\u043e \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440\u0430\u043c."
        )
        return

    parts = text.split(maxsplit=2)

    if len(parts) >= 3 and parts[1].lower() == "\u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c":
        rest = parts[2]
        name_end = rest.find(" ")
        if name_end == -1:
            await update.effective_message.reply_text(
                "\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435: !\u0448\u0430\u0431\u043b\u043e\u043d \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c <\u0438\u043c\u044f> <\u0442\u0435\u043a\u0441\u0442>"
            )
            return
        tpl_name = rest[:name_end]
        tpl_content = rest[name_end + 1:]
        save_template(tpl_name, tpl_content)
        await update.effective_message.reply_text(
            f"\u0428\u0430\u0431\u043b\u043e\u043d '{tpl_name}' \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d."
        )
        return

    if len(parts) >= 2 and parts[1].lower() == "\u0443\u0434\u0430\u043b\u0438\u0442\u044c":
        if len(parts) >= 3:
            if delete_template(parts[2]):
                await update.effective_message.reply_text(
                    f"\u0428\u0430\u0431\u043b\u043e\u043d '{parts[2]}' \u0443\u0434\u0430\u043b\u0435\u043d."
                )
            else:
                await update.effective_message.reply_text(
                    "\u0428\u0430\u0431\u043b\u043e\u043d \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d."
                )
            return
        await update.effective_message.reply_text(
            "\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435: !\u0448\u0430\u0431\u043b\u043e\u043d \u0443\u0434\u0430\u043b\u0438\u0442\u044c <\u0438\u043c\u044f>"
        )
        return

    if len(parts) >= 2:
        content = get_template(parts[1])
        if content:
            await update.effective_message.reply_text(content)
            return
        await update.effective_message.reply_text(
            f"\u0428\u0430\u0431\u043b\u043e\u043d '{parts[1]}' \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d."
        )
        return

    templates = get_all_templates()
    if templates:
        lines = ["\u0428\u0430\u0431\u043b\u043e\u043d\u044b:"]
        for t in templates:
            preview = t["content"][:50]
            lines.append(f"  !\u0448\u0430\u0431\u043b\u043e\u043d {t['name']} -- {preview}...")
        await update.effective_message.reply_text("\n".join(lines))
    else:
        await update.effective_message.reply_text(
            "\u041d\u0435\u0442 \u0448\u0430\u0431\u043b\u043e\u043d\u043e\u0432.\n\n"
            "\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435:\n"
            "!\u0448\u0430\u0431\u043b\u043e\u043d \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c <\u0438\u043c\u044f> <\u0442\u0435\u043a\u0441\u0442> - \u0441\u043e\u0437\u0434\u0430\u0442\u044c\n"
            "!\u0448\u0430\u0431\u043b\u043e\u043d <\u0438\u043c\u044f> - \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u044c\n"
            "!\u0448\u0430\u0431\u043b\u043e\u043d \u0443\u0434\u0430\u043b\u0438\u0442\u044c <\u0438\u043c\u044f> - \u0443\u0434\u0430\u043b\u0438\u0442\u044c"
        )


async def _handle_note_command(update: Update, text: str) -> None:
    if update.effective_message is None or update.effective_user is None:
        return

    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.effective_message.reply_text(
            "\u042d\u0442\u0430 \u043a\u043e\u043c\u0430\u043d\u0434\u0430 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430 \u0442\u043e\u043b\u044c\u043a\u043e \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440\u0430\u043c."
        )
        return

    parts = text.split(maxsplit=2)
    if len(parts) >= 3:
        target = parts[1]
        note_text = parts[2]
        add_note(target, note_text, user_id)
        await update.effective_message.reply_text(
            f"\u0417\u0430\u043c\u0435\u0442\u043a\u0430 \u043e {target} \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0430."
        )
        return

    if len(parts) >= 2:
        target = parts[1]
        notes = get_notes(target)
        if notes:
            lines = [f"\u0417\u0430\u043c\u0435\u0442\u043a\u0438 \u043e {target}:"]
            for n in notes:
                lines.append(f"  - {n['text']}")
            await update.effective_message.reply_text("\n".join(lines))
        else:
            await update.effective_message.reply_text(
                f"\u041d\u0435\u0442 \u0437\u0430\u043c\u0435\u0442\u043e\u043a \u043e {target}."
            )
        return

    await update.effective_message.reply_text(
        "\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435:\n"
        "!\u0437\u0430\u043c\u0435\u0442\u043a\u0430 @user \u0442\u0435\u043a\u0441\u0442 - \u0434\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u0437\u0430\u043c\u0435\u0442\u043a\u0443\n"
        "!\u0437\u0430\u043c\u0435\u0442\u043a\u0430 @user - \u043f\u043e\u0441\u043c\u043e\u0442\u0440\u0435\u0442\u044c \u0437\u0430\u043c\u0435\u0442\u043a\u0438"
    )


async def _handle_translate_command(update: Update, text: str) -> None:
    if update.effective_message is None:
        return

    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        await update.effective_message.reply_text(
            "\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435: !\u043f\u0435\u0440\u0435\u0432\u043e\u0434 <\u044f\u0437\u044b\u043a> <\u0442\u0435\u043a\u0441\u0442>\n"
            "\u041f\u0440\u0438\u043c\u0435\u0440\u044b:\n"
            "!\u043f\u0435\u0440\u0435\u0432\u043e\u0434 en \u041f\u0440\u0438\u0432\u0435\u0442 \u043c\u0438\u0440\n"
            "!\u043f\u0435\u0440\u0435\u0432\u043e\u0434 ru Hello world\n"
            "!\u043f\u0435\u0440\u0435\u0432\u043e\u0434 ja \u0414\u043e\u0431\u0440\u043e \u043f\u043e\u0436\u0430\u043b\u043e\u0432\u0430\u0442\u044c"
        )
        return

    target_lang = parts[1]
    source_text = parts[2]

    result = await translate_text(source_text, target_lang)
    if result:
        await update.effective_message.reply_text(
            f"\u041f\u0435\u0440\u0435\u0432\u043e\u0434 ({target_lang}):\n{result}"
        )
    else:
        await update.effective_message.reply_text(
            "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0432\u044b\u043f\u043e\u043b\u043d\u0438\u0442\u044c \u043f\u0435\u0440\u0435\u0432\u043e\u0434."
        )


async def _handle_summary_command(update: Update) -> None:
    if update.effective_message is None:
        return

    chat_id = update.effective_message.chat_id
    history = get_chat_history(chat_id)

    if not history:
        await update.effective_message.reply_text(
            "\u041d\u0435\u0442 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0439 \u0434\u043b\u044f \u0440\u0435\u0437\u044e\u043c\u0435."
        )
        return

    summary = await summarize_chat(history)
    if summary:
        await update.effective_message.reply_text(
            f"\u0420\u0435\u0437\u044e\u043c\u0435 \u0447\u0430\u0442\u0430:\n\n{summary}"
        )
    else:
        await update.effective_message.reply_text(
            "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u043e\u0437\u0434\u0430\u0442\u044c \u0440\u0435\u0437\u044e\u043c\u0435."
        )


async def _handle_reminder_command(
    update: Update, text: str, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.effective_message is None or update.effective_user is None:
        return

    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        await update.effective_message.reply_text(
            "\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435: !\u043d\u0430\u043f\u043e\u043c\u043d\u0438 <\u0432\u0440\u0435\u043c\u044f> <\u0442\u0435\u043a\u0441\u0442>\n"
            "\u041f\u0440\u0438\u043c\u0435\u0440\u044b:\n"
            "!\u043d\u0430\u043f\u043e\u043c\u043d\u0438 5\u043c \u041f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c \u0437\u0430\u043a\u0430\u0437\u044b\n"
            "!\u043d\u0430\u043f\u043e\u043c\u043d\u0438 2\u0447 \u041f\u043e\u0437\u0432\u043e\u043d\u0438\u0442\u044c \u043a\u043b\u0438\u0435\u043d\u0442\u0443\n"
            "!\u043d\u0430\u043f\u043e\u043c\u043d\u0438 30\u0441 \u0422\u0435\u0441\u0442"
        )
        return

    time_str = parts[1].lower()
    remind_text = parts[2]

    match = re.match(r"(\d+)(\u0441|\u043c|\u0447|s|m|h)", time_str)
    if not match:
        await update.effective_message.reply_text(
            "\u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u0444\u043e\u0440\u043c\u0430\u0442 \u0432\u0440\u0435\u043c\u0435\u043d\u0438. \u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435: 5\u043c, 2\u0447, 30\u0441"
        )
        return

    amount = int(match.group(1))
    unit = match.group(2)
    multipliers = {
        "\u0441": 1, "s": 1, "\u043c": 60, "m": 60, "\u0447": 3600, "h": 3600,
    }
    delay_seconds = amount * multipliers[unit]

    remind_at = time.time() + delay_seconds
    chat_id = update.effective_message.chat_id
    user_id = update.effective_user.id

    add_reminder(chat_id, user_id, remind_text, remind_at)

    unit_names = {
        "\u0441": "\u0441\u0435\u043a", "s": "\u0441\u0435\u043a", "\u043c": "\u043c\u0438\u043d", "m": "\u043c\u0438\u043d",
        "\u0447": "\u0447", "h": "\u0447",
    }
    await update.effective_message.reply_text(
        f"\u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435 \u0447\u0435\u0440\u0435\u0437 {amount}{unit_names[unit]}: {remind_text}"
    )


async def _handle_status_command(update: Update) -> None:
    if update.effective_message is None or update.effective_user is None:
        return

    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.effective_message.reply_text(
            "\u042d\u0442\u0430 \u043a\u043e\u043c\u0430\u043d\u0434\u0430 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430 \u0442\u043e\u043b\u044c\u043a\u043e \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440\u0430\u043c."
        )
        return

    auto_reply_status = "\u0412\u041a\u041b" if is_auto_reply_on() else "\u0412\u042b\u041a\u041b"
    auth_count = get_authenticated_user_count()
    mod_stats = get_moderation_stats_today()
    uptime_seconds = int(time.time() - BOT_START_TIME)
    hours = uptime_seconds // 3600
    minutes = (uptime_seconds % 3600) // 60
    seconds = uptime_seconds % 60
    bot_name = _get_bot_name()

    reply_target = get_setting("reply_target") or "all"
    target_names = {
        "all": "\u0432\u0441\u0435\u043c", "admin": "\u0430\u0434\u043c\u0438\u043d\u0430\u043c", "owner": "\u0432\u043b\u0430\u0434\u0435\u043b\u044c\u0446\u0443",
    }

    connections = get_active_business_connections()
    biz_text = f"\u0411\u0438\u0437\u043d\u0435\u0441-\u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u0439: {len(connections)}"
    if connections:
        for c in connections:
            can = "\u0434\u0430" if c["can_reply"] else "\u043d\u0435\u0442"
            biz_text += (
                f"\n   - @{c['owner_username']} (\u043e\u0442\u0432\u0435\u0442\u044b: {can})"
            )

    bl = get_blacklist()
    templates = get_all_templates()

    dashboard = (
        f"\U0001f4ca \u041f\u0430\u043d\u0435\u043b\u044c \u0443\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u044f -- {bot_name}\n"
        "=" * 30 + "\n"
        f"\U0001f504 \u0410\u0432\u0442\u043e-\u043e\u0442\u0432\u0435\u0442: {auto_reply_status}\n"
        f"\U0001f3af \u041e\u0442\u0432\u0435\u0447\u0430\u0442\u044c: {target_names.get(reply_target, reply_target)}\n"
        f"\U0001f465 \u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u0435\u0439: {auth_count}\n"
        f"\U0001f6e1 \u041c\u043e\u0434\u0435\u0440\u0430\u0446\u0438\u044f: {mod_stats['moderated']} / "
        f"{mod_stats['blocked']} \u0437\u0430\u0431\u043b\u043e\u043a.\n"
        f"\U0001f517 {biz_text}\n"
        f"\U0001f6ab \u0427\u0435\u0440\u043d\u044b\u0439 \u0441\u043f\u0438\u0441\u043e\u043a: {len(bl)}\n"
        f"\U0001f4dd \u0428\u0430\u0431\u043b\u043e\u043d\u043e\u0432: {len(templates)}\n"
        f"\u23f1 \u0410\u043f\u0442\u0430\u0439\u043c: {hours}\u0447 {minutes}\u043c {seconds}\u0441\n"
        "=" * 30
    )

    await update.effective_message.reply_text(dashboard)
