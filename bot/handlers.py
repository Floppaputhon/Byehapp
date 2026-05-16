import logging
import time

from telegram import Update
from telegram.ext import ContextTypes

from bot.database import (
    add_message,
    authenticate_user,
    get_active_business_connections,
    get_authenticated_user_count,
    get_business_connection_owner,
    get_chat_history,
    get_moderation_stats_today,
    is_admin,
    is_auto_reply_on,
    is_user_authenticated,
    log_moderation,
    save_business_connection,
    set_setting,
)
from bot.llm_client import generate_reply, moderate_message

logger = logging.getLogger(__name__)

BOT_START_TIME = time.time()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_user is None:
        return

    user_id = update.effective_user.id

    if is_user_authenticated(user_id):
        await update.effective_message.reply_text(
            "Вы уже авторизованы! Бот готов к работе.\n\n"
            "Доступные команды:\n"
            "!ответ вкл - включить авто-ответы\n"
            "!ответ выкл - выключить авто-ответы\n"
            "!status - панель статуса"
        )
        return

    await update.effective_message.reply_text(
        "Добро пожаловать! Для доступа к боту введите пароль:"
    )


async def handle_business_connection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    status = "подключен" if connection.is_enabled else "отключен"
    logger.info(
        "Business connection %s: user %d (@%s), status=%s, can_reply=%s",
        connection.id, owner_id, owner_username, status, connection.can_reply,
    )

    if connection.is_enabled:
        try:
            await context.bot.send_message(
                chat_id=connection.user_chat_id,
                text=(
                    "\u2705 Бизнес-подключение активировано!\n\n"
                    f"Могу отвечать от вашего имени: {'да' if connection.can_reply else 'нет'}\n\n"
                    "Теперь я буду:\n"
                    "\u2022 Модерировать входящие сообщения\n"
                    "\u2022 Отвечать клиентам от вашего имени (если авто-ответы включены)\n\n"
                    "Используйте !ответ вкл чтобы включить авто-ответы."
                ),
            )
        except Exception as e:
            logger.warning("Could not send business connection notification: %s", e)


async def handle_direct_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    if text.lower().startswith("!ответ"):
        await _handle_auto_reply_command(update, text)
        return

    if text.lower() == "!status":
        await _handle_status_command(update)
        return

    chat_id = message.chat_id
    add_message(chat_id, user_id, "user", text)

    history = get_chat_history(chat_id)
    reply_text = await generate_reply(history, text)

    if reply_text:
        add_message(chat_id, 0, "assistant", reply_text)
        await message.reply_text(reply_text)


async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.business_message is None:
        return

    message = update.business_message
    user_id = message.from_user.id if message.from_user else 0
    chat_id = message.chat.id
    text = (message.text or "").strip()
    connection_id = message.business_connection_id

    if not text:
        return

    owner_id = get_business_connection_owner(connection_id) if connection_id else None
    if owner_id is not None and user_id == owner_id:
        add_message(chat_id, user_id, "assistant", text)
        return

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

    if is_auto_reply_on():
        history = get_chat_history(chat_id)
        reply_text = await generate_reply(history, text)
        if reply_text:
            add_message(chat_id, 0, "assistant", reply_text)
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=reply_text,
                    business_connection_id=connection_id,
                )
            except Exception as e:
                logger.warning("Could not reply to business message: %s", e)


async def _handle_auth(update: Update, text: str) -> None:
    from bot.config import AUTH_PASSWORD

    if update.effective_message is None or update.effective_user is None:
        return

    user_id = update.effective_user.id
    username = update.effective_user.username

    if text == AUTH_PASSWORD:
        authenticate_user(user_id, username, as_admin=True)
        await update.effective_message.reply_text(
            "Пароль верный! Вы авторизованы как администратор.\n\n"
            "Доступные команды:\n"
            "!ответ вкл - включить авто-ответы\n"
            "!ответ выкл - выключить авто-ответы\n"
            "!status - панель статуса"
        )
    else:
        await update.effective_message.reply_text(
            "Неверный пароль. Попробуйте ещё раз."
        )


async def _handle_auto_reply_command(update: Update, text: str) -> None:
    if update.effective_message is None or update.effective_user is None:
        return

    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.effective_message.reply_text("Эта команда доступна только администраторам.")
        return

    parts = text.lower().split()
    if len(parts) >= 2:
        sub = parts[1]
        if sub == "вкл":
            set_setting("auto_reply", "on")
            await update.effective_message.reply_text("Авто-ответы ВКЛЮЧЕНЫ.")
            return
        elif sub == "выкл":
            set_setting("auto_reply", "off")
            await update.effective_message.reply_text("Авто-ответы ВЫКЛЮЧЕНЫ.")
            return

    status = "ВКЛ" if is_auto_reply_on() else "ВЫКЛ"
    await update.effective_message.reply_text(
        f"Текущий статус авто-ответов: {status}\n\n"
        "Используйте:\n"
        "!ответ вкл - включить\n"
        "!ответ выкл - выключить"
    )


async def _handle_status_command(update: Update) -> None:
    if update.effective_message is None or update.effective_user is None:
        return

    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.effective_message.reply_text("Эта команда доступна только администраторам.")
        return

    auto_reply_status = "ВКЛ" if is_auto_reply_on() else "ВЫКЛ"
    auth_count = get_authenticated_user_count()
    mod_stats = get_moderation_stats_today()
    uptime_seconds = int(time.time() - BOT_START_TIME)
    hours = uptime_seconds // 3600
    minutes = (uptime_seconds % 3600) // 60
    seconds = uptime_seconds % 60

    connections = get_active_business_connections()
    biz_text = f"🔗 Бизнес-подключений: {len(connections)}"
    if connections:
        for c in connections:
            biz_text += f"\n   \u2022 @{c['owner_username']} (ответы: {'да' if c['can_reply'] else 'нет'})"

    dashboard = (
        "📊 Панель управления\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🔄 Авто-ответ: {auto_reply_status}\n"
        f"👥 Авторизованных пользователей: {auth_count}\n"
        f"🛡 Модерация сегодня: {mod_stats['moderated']} проверено / {mod_stats['blocked']} заблокировано\n"
        f"{biz_text}\n"
        f"⏱ Аптайм: {hours}ч {minutes}м {seconds}с\n"
        "━━━━━━━━━━━━━━━━━━"
    )

    await update.effective_message.reply_text(dashboard)
