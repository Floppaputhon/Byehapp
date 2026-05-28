"""Slash commands installed on the master bot.

These commands are owner-only — anyone else gets a polite refusal.
"""

from __future__ import annotations

import html
import logging
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, ContextTypes

from ..core.ai_router import AIRouter
from ..core.orchestrator import AgentOrchestrator, AgentRuntime
from ..providers import get_provider_cls

logger = logging.getLogger(__name__)


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
        app.add_handler(CommandHandler("newagent", self._new_agent))
        app.add_handler(CommandHandler("listagents", self._list_agents))
        app.add_handler(CommandHandler("removeagent", self._remove_agent))
        app.add_handler(CommandHandler("reconfigure", self._reconfigure))
        app.add_handler(CommandHandler("addkey", self._add_key))
        app.add_handler(CommandHandler("listkeys", self._list_keys))
        app.add_handler(CommandHandler("removekey", self._remove_key))
        app.add_handler(CommandHandler("setdefault", self._set_default))

    # ------------------------------------------------------------------
    # Auth

    def _is_owner(self, update: Update) -> bool:
        user = update.effective_user
        return user is not None and user.id == self._owner_id

    async def _refuse_if_stranger(self, update: Update) -> bool:
        if self._is_owner(update):
            return False
        await update.message.reply_text(
            "Этот бот — мастер платформы и принимает команды только от своего владельца."
        )
        return True

    # ------------------------------------------------------------------
    # /start, /help

    async def _start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._refuse_if_stranger(update):
            return
        await update.message.reply_text(
            "Я — мастер-бот платформы агентов. Команды:\n\n"
            "/newagent <token> [имя]    — поднять нового сабагента\n"
            "/listagents                — список моих сабагентов\n"
            "/removeagent <id|имя>      — выключить и удалить сабагента\n"
            "/reconfigure <id|имя>      — перезапустить мастер настройки\n\n"
            "/addkey <provider> <api_key> [model] — добавить AI-ключ\n"
            "/listkeys                  — список подключённых AI-ключей\n"
            "/removekey <provider>      — удалить AI-ключ\n"
            "/setdefault <provider> [model] — сделать провайдер дефолтным\n\n"
            "Провайдеры: openai, anthropic, gemini, openrouter, groq, "
            "deepseek, mistral, xai, custom."
        )

    async def _help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._start(update, context)

    # ------------------------------------------------------------------
    # Sub-agents

    async def _new_agent(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._refuse_if_stranger(update):
            return
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "Использование: /newagent <token> [имя]\n"
                "Токен получи в @BotFather. Имя — как тебе удобно ссылаться."
            )
            return
        token = args[0]
        display = args[1] if len(args) > 1 else None
        try:
            record = await self._orch.add_sub_agent(token, display_name=display)
        except ValueError as exc:
            await update.message.reply_text(f"Не получилось: {exc}")
            return
        except Exception as exc:
            logger.exception("Failed to add sub-agent")
            await update.message.reply_text(f"Внутренняя ошибка: {exc}")
            return
        await update.message.reply_text(
            f"Сабагент «{record.display_name}» (@{record.bot_username}) поднят. "
            "Открой чат с ним и нажми /start — он попросит выбрать роль."
        )

    async def _list_agents(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._refuse_if_stranger(update):
            return
        records = await self._orch.list_sub_agents()
        if not records:
            await update.message.reply_text("Пока нет ни одного сабагента. /newagent <token>")
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
        # Wipe onboarding state so next /start in the sub-agent restarts wizard.
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

    # ------------------------------------------------------------------
    # AI keys

    async def _add_key(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._refuse_if_stranger(update):
            return
        args = context.args or []
        if len(args) < 2:
            await update.message.reply_text(
                "Использование: /addkey <provider> <api_key> [default_model]\n"
                "Провайдеры: openai, anthropic, gemini, openrouter, groq, "
                "deepseek, mistral, xai, custom."
            )
            return
        provider = args[0].lower()
        api_key = args[1]
        model = args[2] if len(args) > 2 else None
        if get_provider_cls(provider) is None:
            await update.message.reply_text(f"Не знаю провайдера «{provider}».")
            return
        # Determine whether this is the first key — if so, make it default.
        existing = await self._ai.list_keys(self._owner_id)
        try:
            await self._ai.add_key(
                owner_id=self._owner_id,
                provider=provider,
                api_key=api_key,
                default_model=model,
                make_default=not existing,
            )
        except Exception as exc:
            await update.message.reply_text(f"Не сохранил: {exc}")
            return
        # Best-effort: scrub the message containing the secret.
        try:
            await update.message.delete()
        except Exception:
            pass
        await update.effective_chat.send_message(
            f"Ключ для «{provider}» сохранён зашифрованным."
            + ("\n(сообщение с ключом удалить не удалось — удали вручную, пожалуйста)" if update.message else "")
        )

    async def _list_keys(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._refuse_if_stranger(update):
            return
        keys = await self._ai.list_keys(self._owner_id)
        if not keys:
            await update.message.reply_text("AI-ключей пока нет. /addkey <provider> <key>")
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
