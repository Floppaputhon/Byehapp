"""Burmalda Bot — Telegram Business API auto-responder.

Replies to incoming business messages with a warning that the message is
undeliverable until the sender types the magic word "Бурмалда". When the
magic word is sent, confirms that the recipient saw the message.

Requires:
- Telegram Premium account with Telegram Business enabled
- Bot must be added under Settings → Telegram Business → Chatbots
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ContextTypes,
    TypeHandler,
)

MAGIC_WORD = "бурмалда"

WARNING_TEXT = (
    "⚠️ Ваше сообщение недоступно для собеседника. "
    "Для его доставки необходимо ввести слово «Бурмалда»"
)
SEEN_TEXT = "Собеседник увидел ваше сообщение ✅"

logger = logging.getLogger(__name__)


def _extract_text(message) -> str:
    return (message.text or message.caption or "").strip()


def _contains_magic_word(text: str) -> bool:
    return MAGIC_WORD in text.casefold()


async def handle_business_connection(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Track business connections so we know who the account owner is."""
    conn = update.business_connection
    if conn is None:
        return

    context.bot_data.setdefault("owners", {})[conn.id] = conn.user.id
    logger.info(
        "Business connection update: id=%s owner=%s enabled=%s",
        conn.id,
        conn.user.id,
        getattr(conn, "is_enabled", None),
    )


def _owner_id_for(context: ContextTypes.DEFAULT_TYPE, connection_id: str) -> Optional[int]:
    return context.bot_data.get("owners", {}).get(connection_id)


async def handle_business_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Auto-reply to incoming business messages."""
    message = update.business_message
    if message is None:
        return

    connection_id = message.business_connection_id
    if not connection_id:
        return

    sender = message.from_user
    if sender is None:
        return

    # Skip messages sent by the business account owner themselves.
    owner_id = _owner_id_for(context, connection_id)
    if owner_id is not None and sender.id == owner_id:
        return
    # Fallback heuristic for the case where we never received a
    # business_connection update for this id (e.g. bot restarted): the
    # owner's outgoing messages always have from_user.is_bot == False AND
    # message.chat.id == sender.id is NOT a reliable marker, so we rely
    # on the explicit owner mapping only.

    text = _extract_text(message)
    if _contains_magic_word(text):
        reply = SEEN_TEXT
    else:
        reply = WARNING_TEXT

    try:
        await context.bot.send_message(
            chat_id=message.chat.id,
            text=reply,
            business_connection_id=connection_id,
        )
    except Exception:
        logger.exception("Failed to send auto-reply")


async def handle_edited_business_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Treat edited messages the same as new ones."""
    if update.edited_business_message is None:
        return
    # Re-route through the same logic by wrapping into a fake update.
    fake_update = Update(
        update_id=update.update_id,
        business_message=update.edited_business_message,
    )
    await handle_business_message(fake_update, context)


async def _on_any_update(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch updates ourselves so we don't depend on filter helpers."""
    if not isinstance(update, Update):
        return
    if update.business_connection is not None:
        await handle_business_connection(update, context)
    elif update.business_message is not None:
        await handle_business_message(update, context)
    elif update.edited_business_message is not None:
        await handle_edited_business_message(update, context)


def build_application(token: str) -> Application:
    app = ApplicationBuilder().token(token).build()
    # A single TypeHandler keeps things simple and version-tolerant: we
    # branch on which field of the Update is populated.
    app.add_handler(TypeHandler(Update, _on_any_update))
    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN environment variable is required")

    app = build_application(token)
    logger.info("Starting Burmalda bot (long polling)…")
    app.run_polling(
        allowed_updates=[
            "business_connection",
            "business_message",
            "edited_business_message",
            "message",
        ],
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
