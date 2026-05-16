import logging

from telegram.ext import Application, BusinessConnectionHandler, CommandHandler, MessageHandler, filters

from bot.config import TELEGRAM_BOT_TOKEN
from bot.database import init_db
from bot.handlers import handle_business_connection, handle_business_message, handle_direct_message, start_command

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
            filters.TEXT & ~filters.COMMAND & filters.UpdateType.BUSINESS_MESSAGE,
            handle_business_message,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE
            & ~filters.UpdateType.BUSINESS_MESSAGE,
            handle_direct_message,
        )
    )

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


if __name__ == "__main__":
    main()
