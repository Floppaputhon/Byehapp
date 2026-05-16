#!/usr/bin/env python3
"""Telegram Business AI Assistant Bot — entry point."""

import logging

from telegram.ext import (
    Application,
    BusinessConnectionHandler,
    BusinessMessagesDeletedHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from config import BOT_TOKEN
import database as db
import handlers

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not set! Create a .env file with BOT_TOKEN=...")
        return

    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=60.0,
        write_timeout=30.0,
        pool_timeout=10.0,
    )
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request)
        .build()
    )

    # Business API handlers
    app.add_handler(BusinessConnectionHandler(handlers.handle_business_connection))
    app.add_handler(
        BusinessMessagesDeletedHandler(handlers.handle_deleted_business_messages)
    )
    app.add_handler(
        MessageHandler(
            filters.UpdateType.BUSINESS_MESSAGE, handlers.handle_business_message
        )
    )
    app.add_handler(
        MessageHandler(
            filters.UpdateType.EDITED_BUSINESS_MESSAGE,
            handlers.handle_edited_business_message,
        )
    )

    # Command handlers (direct messages to the bot)
    app.add_handler(CommandHandler("start", handlers.cmd_start))
    app.add_handler(CommandHandler("help", handlers.cmd_help))
    app.add_handler(CommandHandler("deleted", handlers.cmd_deleted))
    app.add_handler(CommandHandler("summary", handlers.cmd_summary))
    app.add_handler(CommandHandler("remind", handlers.cmd_remind))
    app.add_handler(CommandHandler("pending", handlers.cmd_pending))
    app.add_handler(CommandHandler("stats", handlers.cmd_stats))
    app.add_handler(CommandHandler("search", handlers.cmd_search))
    app.add_handler(CommandHandler("translate", handlers.cmd_translate))
    app.add_handler(CommandHandler("analyze", handlers.cmd_analyze))
    app.add_handler(CommandHandler("myreminders", handlers.cmd_myreminders))
    app.add_handler(CommandHandler("autoreply", handlers.cmd_autoreply))
    app.add_handler(CommandHandler("note", handlers.cmd_note))
    app.add_handler(CommandHandler("broadcast", handlers.cmd_broadcast))
    app.add_handler(CommandHandler("contact", handlers.cmd_contact))
    app.add_handler(CommandHandler("export", handlers.cmd_export))
    app.add_handler(CommandHandler("qr", handlers.cmd_qr))
    app.add_handler(CommandHandler("priority", handlers.cmd_priority))
    app.add_handler(CommandHandler("whoami", handlers.cmd_whoami))
    app.add_handler(CommandHandler("access", handlers.cmd_access))
    app.add_handler(CommandHandler("chats", handlers.cmd_chats))
    app.add_handler(CommandHandler("groups", handlers.cmd_groups))

    # Moderation commands
    app.add_handler(CommandHandler("moder", handlers.cmd_moder))
    app.add_handler(CommandHandler("rules", handlers.cmd_rules))
    app.add_handler(CommandHandler("warn", handlers.cmd_warn))
    app.add_handler(CommandHandler("mute", handlers.cmd_mute))
    app.add_handler(CommandHandler("unmute", handlers.cmd_unmute))
    app.add_handler(CommandHandler("kick", handlers.cmd_kick))
    app.add_handler(CommandHandler("ban", handlers.cmd_ban))
    app.add_handler(CommandHandler("unban", handlers.cmd_unban))
    app.add_handler(CommandHandler("unwarn", handlers.cmd_unwarn))
    app.add_handler(CommandHandler("warnlist", handlers.cmd_warnlist))
    app.add_handler(CommandHandler("warnlimit", handlers.cmd_warnlimit))
    app.add_handler(CommandHandler("whoadmin", handlers.cmd_whoadmin))
    app.add_handler(CommandHandler("pin", handlers.cmd_pin))
    app.add_handler(CommandHandler("unpin", handlers.cmd_unpin))
    app.add_handler(CommandHandler("poll", handlers.cmd_poll))
    app.add_handler(CommandHandler("report", handlers.cmd_report))

    # Group-specific commands
    app.add_handler(CommandHandler("groupstats", handlers.cmd_groupstats))
    app.add_handler(CommandHandler("groupsummary", handlers.cmd_groupsummary))
    app.add_handler(CommandHandler("top", handlers.cmd_top))
    app.add_handler(CommandHandler("groupsearch", handlers.cmd_groupsearch))

    # Photo message handler
    app.add_handler(
        MessageHandler(
            filters.PHOTO & filters.ChatType.PRIVATE,
            handlers.handle_photo_message,
        )
    )

    # Voice message handler
    app.add_handler(
        MessageHandler(
            (filters.VOICE | filters.AUDIO) & filters.ChatType.PRIVATE,
            handlers.handle_voice_message,
        )
    )

    # Free-form AI Q&A — must be AFTER command handlers
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            handlers.handle_direct_question,
        )
    )

    # New member welcome handler
    app.add_handler(
        MessageHandler(
            filters.StatusUpdate.NEW_CHAT_MEMBERS,
            handlers.handle_new_member,
        ),
    )

    # Iris-style text commands in groups (!, ., Ирис, варн, бан, мут...)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
            handlers.handle_iris_command,
        ),
        group=1,
    )

    # Group message tracking + AI Q&A (mention/reply to bot)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
            handlers.handle_group_question,
        ),
        group=2,
    )
    app.add_handler(
        MessageHandler(
            filters.ALL & filters.ChatType.GROUPS,
            handlers.handle_group_message,
        ),
        group=2,
    )

    # Periodic reminder check every 60 seconds
    job_queue = app.job_queue
    job_queue.run_repeating(handlers.check_reminders, interval=60, first=10)
    job_queue.run_repeating(handlers.check_scheduled_messages, interval=30, first=15)

    # Initialize DB on startup
    async def post_init(application: Application) -> None:
        await db.init_db()
        logger.info("Database initialized")

    app.post_init = post_init

    async def error_handler(update, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Exception while handling update: %s", context.error)

    app.add_error_handler(error_handler)

    logger.info("Bot starting...")
    app.run_polling(
        allowed_updates=[
            "message",
            "edited_message",
            "business_connection",
            "business_message",
            "edited_business_message",
            "deleted_business_messages",
        ],
        drop_pending_updates=True,
        pool_timeout=30,
        connect_timeout=30,
        read_timeout=60,
    )


if __name__ == "__main__":
    main()
