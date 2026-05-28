"""Conversational handler for sub-agents.

Every non-command text message flows through this pipeline:

1. The sub-agent only responds AFTER onboarding is complete (otherwise the
   onboarding dispatcher in :mod:`.onboarding` handles the message and the
   chat handler stays silent).
2. The intent router runs first with the platform's tool schemas. If the LLM
   matches a tool, we execute it and return the tool's summary verbatim
   (this is what powers ``"подключи гитхаб"`` etc. without any regex).
3. Otherwise we run a normal chat completion with the agent's compiled
   system prompt + last N messages of history.

A small rolling history is kept per (agent, chat).
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from ..core.ai_router import AIRouter
from ..core.db import Database
from ..core.intent_router import IntentRouter
from ..core.orchestrator import AgentOrchestrator, AgentRuntime
from ..integrations import IntegrationRegistry
from ..providers import ChatMessage

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 12


class ChatDispatcher:
    def __init__(
        self,
        *,
        orchestrator: AgentOrchestrator,
        db: Database,
        ai_router: AIRouter,
        intent_router: IntentRouter,
        integrations: IntegrationRegistry,
        owner_id: int,
    ) -> None:
        self._orch = orchestrator
        self._db = db
        self._ai = ai_router
        self._intent = intent_router
        self._integrations = integrations
        self._owner_id = owner_id

    def install(self, runtime: AgentRuntime) -> None:
        if runtime.is_master:
            return  # master uses commands only
        # Run AFTER onboarding (group=0). Onboarding installs at group=-1.
        runtime.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle),
            group=0,
        )

    async def _handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None or not message.text:
            return

        record = context.bot_data.get("agent_record")
        if record is None:
            return

        # Reload the latest record so we always see the freshest system prompt.
        refreshed = await self._orch.get_record(record.id)
        if refreshed is not None:
            record = refreshed
            context.bot_data["agent_record"] = refreshed

        if record.status != "running" or not record.system_prompt:
            # Onboarding incomplete — silently let the onboarding handler
            # deal with the message.
            return

        user_text = message.text.strip()
        chat_id = message.chat_id
        await self._save_message(record.id, chat_id, "user", user_text)

        # Step 1: LLM-driven intent routing.
        intent = await self._intent.classify(
            owner_id=self._owner_id,
            user_text=user_text,
            tools=self._integrations.tools(),
        )
        if intent.matched_tool:
            result = await self._integrations.dispatch(
                intent.tool_name, self._owner_id, intent.arguments
            )
            reply = result.summary or (
                "Готово." if result.ok else "Не получилось выполнить."
            )
            await message.reply_text(reply)
            await self._save_message(record.id, chat_id, "assistant", reply)
            return

        # Step 2: regular chat.
        history = await self._load_history(record.id, chat_id, limit=HISTORY_LIMIT)
        try:
            chat_result = await self._ai.chat(
                owner_id=self._owner_id,
                system=record.system_prompt,
                messages=history,
                temperature=0.7,
                max_tokens=1024,
            )
        except Exception as exc:
            logger.exception("Chat failed for agent=%s", record.display_name)
            await message.reply_text(f"AI недоступен: {exc}")
            return

        text = chat_result.text or "(пустой ответ)"
        await message.reply_text(text)
        await self._save_message(record.id, chat_id, "assistant", text)

    # ------------------------------------------------------------------
    # Memory

    async def _save_message(
        self, agent_id: int, chat_id: int, role: str, content: str
    ) -> None:
        await self._db.execute(
            "INSERT INTO messages(agent_id, chat_id, role, content) VALUES(?, ?, ?, ?)",
            (agent_id, chat_id, role, content),
        )

    async def _load_history(
        self, agent_id: int, chat_id: int, *, limit: int
    ) -> list[ChatMessage]:
        rows = await self._db.fetchall(
            "SELECT role, content FROM messages "
            "WHERE agent_id = ? AND chat_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (agent_id, chat_id, limit),
        )
        # rows come back newest-first; reverse for chronological order
        return [
            ChatMessage(role=r["role"], content=r["content"])
            for r in reversed(rows)
        ]
