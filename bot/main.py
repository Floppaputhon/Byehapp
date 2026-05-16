import logging
import threading

import uvicorn
from telegram.ext import Application, BusinessConnectionHandler, CommandHandler, MessageHandler, filters

from bot.config import TELEGRAM_BOT_TOKEN
from bot.database import init_db
from bot.handlers import (
    check_reminders,
    handle_business_connection,
    handle_business_message,
    handle_business_voice,
    handle_direct_message,
    handle_group_message,
    handle_voice_message,
    start_command,
)

WEBAPP_PORT = 8080

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set in .env")
        return

    init_db()
    logger.info("Database initialized")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))

    app.add_handler(BusinessConnectionHandler(handle_business_connection))

    app.add_handler(
        MessageHandler(
            (filters.VOICE | filters.AUDIO) & filters.UpdateType.BUSINESS_MESSAGE,
            handle_business_voice,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.UpdateType.BUSINESS_MESSAGE,
            handle_business_message,
        )
    )

    app.add_handler(
        MessageHandler(
            (filters.VOICE | filters.AUDIO) & ~filters.UpdateType.BUSINESS_MESSAGE,
            handle_voice_message,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE
            & ~filters.UpdateType.BUSINESS_MESSAGE & filters.ChatType.PRIVATE,
            handle_direct_message,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE
            & ~filters.UpdateType.BUSINESS_MESSAGE
            & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
            handle_group_message,
        )
    )

    webapp_thread = threading.Thread(
        target=_run_webapp, daemon=True, name="webapp",
    )
    webapp_thread.start()
    logger.info("Mini App server started on port %d", WEBAPP_PORT)

    app.job_queue.run_repeating(check_reminders, interval=15, first=5)
    logger.info("Reminder polling started (every 15s)")

    logger.info("Bot started polling...")
    app.run_polling(
        allowed_updates=[
            "message",
            "business_connection",
            "business_message",
            "edited_business_message",
        ],
        drop_pending_updates=True,
    )


def _run_webapp() -> None:
    from webapp.server import app as webapp_app  # noqa: WPS433

    uvicorn.run(webapp_app, host="0.0.0.0", port=WEBAPP_PORT, log_level="info")


if __name__ == "__main__":
    main()
