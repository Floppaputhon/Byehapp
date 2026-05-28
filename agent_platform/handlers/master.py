"""Slash commands installed on the master bot.

These commands are owner-only — anyone else gets a polite refusal.

Two slash commands (``/addkey`` and ``/newagent``) accept either a one-shot
form with all arguments inline, or a conversational form when called with no
arguments. The conversational form is the recommended UX:

* ``/addkey`` → bot shows inline keyboard with provider buttons → user taps
  one → bot asks for the key in the next message → user pastes the key →
  bot stores it encrypted, deletes the message with the secret, and asks for
  an optional default model.

State for the conversation is kept in :pyattr:`telegram.ext.CallbackContext.user_data`,
which is per-(user, chat) and lives in memory for the duration of the
session. It also includes a timestamp and is reset on any cancel/restart.
"""

from __future__ import annotations

import html
import logging
import time
from typing import Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..core.ai_router import AIRouter
from ..core.orchestrator import AgentOrchestrator, AgentRuntime
from ..providers import PROVIDERS, get_provider_cls

logger = logging.getLogger(__name__)


CONV_KEY = "master_conv"
CONV_TTL_SEC = 15 * 60

# Conversation step names.
S_AWAIT_PROVIDER = "addkey:await_provider"
S_AWAIT_KEY = "addkey:await_key"
S_AWAIT_MODEL = "addkey:await_model"
S_AWAIT_AGENT_TOKEN = "newagent:await_token"
S_AWAIT_AGENT_NAME = "newagent:await_name"

CB_ADDKEY_PROVIDER = "akp:"
CB_ADDKEY_SKIP_MODEL = "akm:skip"
CB_CANCEL = "mcv:cancel"


# Human-friendly hint shown when asking for a key. Helps users figure out
# where to grab it for each provider.
PROVIDER_HINTS = {
    "openai": "https://platform.openai.com/api-keys",
    "anthropic": "https://console.anthropic.com/settings/keys",
    "gemini": "https://aistudio.google.com/apikey",
    "openrouter": "https://openrouter.ai/keys",
    "groq": "https://console.groq.com/keys",
    "deepseek": "https://platform.deepseek.com/api_keys",
    "mistral": "https://console.mistral.ai/api-keys",
    "xai": "https://console.x.ai",
    "custom": "(укажи base_url отдельно; например http://localhost:11434/v1 для Ollama)",
}


class MasterCommands:
    def __init__(
        self,
        *,
        orchestrator: AgentOrchestrator,
        ai_router: AIRouter,
        owner_id: int,
    ) -> None:
        self._orch = orchestrator
        self._ai = ai_router
        self._owner_id = owner_id

    def install(self, runtime: AgentRuntime) -> None:
        if not runtime.is_master:
            return
        app = runtime.application

        app.add_handler(CommandHandler("start", self._start))
        app.add_handler(CommandHandler("help", self._help))
        app.add_handler(CommandHandler("cancel", self._cancel_cmd))

        app.add_handler(CommandHandler("newagent", self._new_agent))
        app.add_handler(CommandHandler("listagents", self._list_agents))
        app.add_handler(CommandHandler("removeagent", self._remove_agent))
        app.add_handler(CommandHandler("reconfigure", self._reconfigure))

        app.add_handler(CommandHandler("addkey", self._add_key))
        app.add_handler(CommandHandler("listkeys", self._list_keys))
        app.add_handler(CommandHandler("removekey", self._remove_key))
        app.add_handler(CommandHandler("setdefault", self._set_default))

        app.add_handler(
            CallbackQueryHandler(self._on_callback, pattern=r"^(akp:|akm:|mcv:)"),
        )
        # Text router only triggers when the owner is mid-conversation,
        # so it does not steal arbitrary chats the master might be in.
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text),
            group=-1,
        )

    # ------------------------------------------------------------------
    # Auth

    def _is_owner(self, update: Update) -> bool:
        user = update.effective_user
        return user is not None and user.id == self._owner_id

    async def _refuse_if_stranger(self, update: Update) -> bool:
        if self._is_owner(update):
            return False
        if update.effective_message is not None:
            await update.effective_message.reply_text(
                "Этот бот — мастер платформы и принимает команды только от своего владельца."
            )
        return True

    # ------------------------------------------------------------------
    # Conversation state helpers

    @staticmethod
    def _conv(user_data: dict) -> dict:
        conv = user_data.get(CONV_KEY)
        if conv is None or (time.time() - conv.get("ts", 0) > CONV_TTL_SEC):
            conv = {"step": None, "data": {}, "ts": time.time()}
            user_data[CONV_KEY] = conv
        return conv

    @staticmethod
    def _set_step(user_data: dict, step: Optional[str], **data) -> None:
        conv = MasterCommands._conv(user_data)
        conv["step"] = step
        conv["data"].update(data)
        conv["ts"] = time.time()

    @staticmethod
    def _clear_conv(user_data: dict) -> None:
        user_data.pop(CONV_KEY, None)

    @staticmethod
    def _cancel_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Отмена", callback_data=CB_CANCEL)]]
        )

    # ------------------------------------------------------------------
    # /start, /help, /cancel

    async def _start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._refuse_if_stranger(update):
            return
        self._clear_conv(context.user_data)
        await update.message.reply_text(
            "Я — мастер-бот платформы агентов. Команды:\n\n"
            "🔑 <b>AI-ключи</b>\n"
            "/addkey  — добавить AI-ключ (диалог с кнопками)\n"
            "/listkeys — что уже подключено\n"
            "/removekey <provider> — удалить\n"
            "/setdefault <provider> [model] — дефолтный провайдер\n\n"
            "🤖 <b>Сабагенты</b>\n"
            "/newagent — создать сабагента (диалог)\n"
            "/listagents — список сабагентов\n"
            "/removeagent <id|имя> — удалить\n"
            "/reconfigure <id|имя> — перенастроить персону\n\n"
            "/cancel — прервать любой диалог\n\n"
            f"Поддерживаемые AI: {', '.join(sorted(PROVIDERS.keys()))}.",
            parse_mode=ParseMode.HTML,
        )

    async def _help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._start(update, context)

    async def _cancel_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._refuse_if_stranger(update):
            return
        had_conv = context.user_data.get(CONV_KEY) is not None
        self._clear_conv(context.user_data)
        await update.message.reply_text(
            "Диалог отменён." if had_conv else "Активного диалога не было."
        )

    # ------------------------------------------------------------------
    # /addkey  (two modes: inline args OR conversational)

    async def _add_key(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._refuse_if_stranger(update):
            return
        args = context.args or []

        # Inline form (kept for power users / scripts).
        if len(args) >= 2:
            await self._addkey_inline(update, context, args)
            return

        # Conversational form.
        provider_buttons: list[list[InlineKeyboardButton]] = []
        # Two providers per row for compactness.
        items = sorted(PROVIDERS.keys())
        for i in range(0, len(items), 2):
            row = [
                InlineKeyboardButton(
                    name.upper() if len(name) <= 4 else name.capitalize(),
                    callback_data=f"{CB_ADDKEY_PROVIDER}{name}",
                )
                for name in items[i : i + 2]
            ]
            provider_buttons.append(row)
        provider_buttons.append(
            [InlineKeyboardButton("❌ Отмена", callback_data=CB_CANCEL)]
        )

        self._set_step(context.user_data, S_AWAIT_PROVIDER)
        await update.message.reply_text(
            "Какой AI-провайдер подключить? Нажми на кнопку.",
            reply_markup=InlineKeyboardMarkup(provider_buttons),
        )

    async def _addkey_inline(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        args: list[str],
    ) -> None:
        provider = args[0].lower()
        api_key = args[1]
        model = args[2] if len(args) > 2 else None
        if get_provider_cls(provider) is None:
            await update.message.reply_text(f"Не знаю провайдера «{provider}».")
            return
        await self._save_key(update, context, provider, api_key, model)

    # ------------------------------------------------------------------
    # /newagent  (two modes too)

    async def _new_agent(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._refuse_if_stranger(update):
            return
        args = context.args or []
        if args:
            token = args[0]
            display = args[1] if len(args) > 1 else None
            await self._save_agent(update, context, token, display)
            return

        self._set_step(context.user_data, S_AWAIT_AGENT_TOKEN)
        await update.message.reply_text(
            "Пришли мне токен нового бота от @BotFather одним сообщением.\n"
            "Я сохраню его зашифрованным и сразу попробую запустить.",
            reply_markup=self._cancel_keyboard(),
        )

    # ------------------------------------------------------------------
    # Callback handler (provider selection / skip / cancel)

    async def _on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._refuse_if_stranger(update):
            return
        query = update.callback_query
        await query.answer()
        data = query.data or ""

        if data == CB_CANCEL:
            self._clear_conv(context.user_data)
            await query.edit_message_text("Диалог отменён.")
            return

        if data.startswith(CB_ADDKEY_PROVIDER):
            provider = data[len(CB_ADDKEY_PROVIDER):].lower()
            if get_provider_cls(provider) is None:
                await query.edit_message_text(f"Неизвестный провайдер: {provider}")
                self._clear_conv(context.user_data)
                return
            self._set_step(context.user_data, S_AWAIT_KEY, provider=provider)
            hint = PROVIDER_HINTS.get(provider, "")
            await query.edit_message_text(
                f"Подключаем <b>{html.escape(provider)}</b>.\n\n"
                f"Пришли мне API-ключ одним сообщением.\n"
                f"{('Где взять: ' + hint) if hint else ''}\n\n"
                "Ключ будет немедленно зашифрован, а исходное сообщение я "
                "постараюсь удалить из чата.",
                parse_mode=ParseMode.HTML,
                reply_markup=self._cancel_keyboard(),
            )
            return

        if data == CB_ADDKEY_SKIP_MODEL:
            conv = self._conv(context.user_data)
            provider = conv["data"].get("provider", "")
            self._clear_conv(context.user_data)
            cls = get_provider_cls(provider)
            default_model = cls.default_model if cls else "?"
            await query.edit_message_text(
                f"Готово. Буду использовать дефолтную модель «{default_model}» "
                f"для {provider}."
            )
            return

    # ------------------------------------------------------------------
    # Text router — only acts when there is an active conversation

    async def _on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            return  # ignore strangers entirely; no leakage about state.
        conv = context.user_data.get(CONV_KEY)
        if not conv or not conv.get("step"):
            return
        step = conv["step"]
        text = (update.message.text or "").strip()

        if step == S_AWAIT_KEY:
            provider = conv["data"].get("provider")
            if not provider or not text:
                await update.message.reply_text("Пустой ключ — отмена.")
                self._clear_conv(context.user_data)
                return
            await self._save_key(update, context, provider, text, model=None)
            return

        if step == S_AWAIT_MODEL:
            provider = conv["data"].get("provider")
            self._clear_conv(context.user_data)
            if not provider or not text:
                return
            updated = await self._ai.update_model(
                owner_id=self._owner_id,
                provider=provider,
                model=text,
            )
            await update.message.reply_text(
                f"Готово. {provider} → модель «{text}»."
                if updated
                else f"Не нашёл сохранённый ключ для {provider}."
            )
            return

        if step == S_AWAIT_AGENT_TOKEN:
            await self._save_agent(update, context, text, None)
            return

    # ------------------------------------------------------------------
    # Persistence helpers

    async def _save_key(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        provider: str,
        api_key: str,
        model: Optional[str],
    ) -> None:
        existing = await self._ai.list_keys(self._owner_id)
        make_default = not existing
        try:
            await self._ai.add_key(
                owner_id=self._owner_id,
                provider=provider,
                api_key=api_key,
                default_model=model,
                make_default=make_default,
            )
        except Exception as exc:
            logger.exception("Failed to store API key for %s", provider)
            await update.message.reply_text(f"Не сохранил: {exc}")
            self._clear_conv(context.user_data)
            return

        # Try to wipe the message containing the secret.
        deleted = False
        try:
            await update.message.delete()
            deleted = True
        except Exception:
            pass

        cls = get_provider_cls(provider)
        default_model = cls.default_model if cls else ""
        confirmation = (
            f"✅ Ключ для <b>{html.escape(provider)}</b> сохранён зашифрованным."
            + (
                ""
                if deleted
                else "\n⚠️ Я не смог удалить сообщение с ключом — удали его, пожалуйста, вручную."
            )
        )
        if model is None:
            # Offer to set a custom default model. We only remember which
            # provider we are tuning — the plaintext key is NEVER kept in
            # user_data (it lives encrypted in the DB at this point).
            self._set_step(
                context.user_data,
                S_AWAIT_MODEL,
                provider=provider,
            )
            kbd = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            f"Оставить «{default_model}»",
                            callback_data=CB_ADDKEY_SKIP_MODEL,
                        )
                    ],
                    [InlineKeyboardButton("❌ Отмена", callback_data=CB_CANCEL)],
                ]
            )
            await update.effective_chat.send_message(
                confirmation
                + "\n\nХочешь задать конкретную модель по умолчанию? Пришли её "
                "одним сообщением (например <code>gpt-4o</code>), или нажми «Оставить».",
                parse_mode=ParseMode.HTML,
                reply_markup=kbd,
            )
        else:
            self._clear_conv(context.user_data)
            await update.effective_chat.send_message(
                confirmation + f"\nМодель: <code>{html.escape(model)}</code>.",
                parse_mode=ParseMode.HTML,
            )

    async def _save_agent(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        token: str,
        display_name: Optional[str],
    ) -> None:
        token = token.strip()
        if not token:
            await update.message.reply_text("Пустой токен — отмена.")
            self._clear_conv(context.user_data)
            return
        try:
            record = await self._orch.add_sub_agent(token, display_name=display_name)
        except ValueError as exc:
            await update.message.reply_text(f"Не получилось: {exc}")
            return
        except Exception as exc:
            logger.exception("Failed to add sub-agent")
            await update.message.reply_text(f"Внутренняя ошибка: {exc}")
            self._clear_conv(context.user_data)
            return

        # Try to remove the token from the chat.
        try:
            await update.message.delete()
        except Exception:
            pass

        self._clear_conv(context.user_data)
        await update.effective_chat.send_message(
            f"✅ Сабагент «{html.escape(record.display_name)}» "
            f"(@{html.escape(record.bot_username or '?')}) поднят. "
            "Открой чат с ним и нажми /start — он попросит выбрать роль.",
            parse_mode=ParseMode.HTML,
        )

    # ------------------------------------------------------------------
    # Read-only commands

    async def _list_agents(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._refuse_if_stranger(update):
            return
        records = await self._orch.list_sub_agents()
        if not records:
            await update.message.reply_text("Пока нет ни одного сабагента. /newagent")
            return
        lines: list[str] = []
        for r in records:
            persona = (r.persona.get("purpose") if r.persona_json else None) or "—"
            uname = f"@{r.bot_username}" if r.bot_username else "(нет)"
            lines.append(
                f"#{r.id} • <b>{html.escape(r.display_name)}</b> • {uname} • "
                f"роль: {persona} • статус: {r.status}"
            )
        await update.message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
        )

    async def _remove_agent(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._refuse_if_stranger(update):
            return
        ident = (context.args or [""])[0]
        record = await self._resolve_agent(ident)
        if record is None:
            await update.message.reply_text("Не нашёл такого сабагента.")
            return
        await self._orch.remove_sub_agent(record.id)
        await update.message.reply_text(f"Удалил «{record.display_name}».")

    async def _reconfigure(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._refuse_if_stranger(update):
            return
        ident = (context.args or [""])[0]
        record = await self._resolve_agent(ident)
        if record is None:
            await update.message.reply_text("Не нашёл такого сабагента.")
            return
        await self._orch.update_status(record.id, "configuring")
        from .onboarding import OnboardingDispatcher  # local import to avoid cycle
        dispatcher = context.bot_data.get("onboarding_dispatcher")
        if isinstance(dispatcher, OnboardingDispatcher):
            await dispatcher.reset(record.id)
        await update.message.reply_text(
            f"OK, у «{record.display_name}» сброшена настройка. "
            "Открой чат с ним и нажми /start, чтобы пройти мастер заново."
        )

    async def _resolve_agent(self, ident: str):
        records = await self._orch.list_sub_agents()
        if not ident:
            return None
        ident = ident.strip()
        if ident.isdigit():
            num = int(ident)
            return next((r for r in records if r.id == num), None)
        return next((r for r in records if r.display_name == ident), None)

    async def _list_keys(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._refuse_if_stranger(update):
            return
        keys = await self._ai.list_keys(self._owner_id)
        if not keys:
            await update.message.reply_text("AI-ключей пока нет. /addkey")
            return
        lines = []
        for k in keys:
            default = " ⭐" if k["is_default"] else ""
            model = k["default_model"] or "—"
            lines.append(f"{k['provider']}{default} • модель: {model}")
        await update.message.reply_text("\n".join(lines))

    async def _remove_key(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._refuse_if_stranger(update):
            return
        args = context.args or []
        if not args:
            await update.message.reply_text("Использование: /removekey <provider>")
            return
        ok = await self._ai.remove_key(owner_id=self._owner_id, provider=args[0].lower())
        await update.message.reply_text("Удалено." if ok else "Не нашёл такой ключ.")

    async def _set_default(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._refuse_if_stranger(update):
            return
        args = context.args or []
        if not args:
            await update.message.reply_text("Использование: /setdefault <provider> [model]")
            return
        provider = args[0].lower()
        model: Optional[str] = args[1] if len(args) > 1 else None
        try:
            await self._ai.set_default(owner_id=self._owner_id, provider=provider, model=model)
        except ValueError as exc:
            await update.message.reply_text(str(exc))
            return
        await update.message.reply_text(
            f"Дефолтный провайдер: {provider}" + (f" / {model}" if model else "")
        )
