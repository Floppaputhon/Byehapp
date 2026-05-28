"""Onboarding wizard for newly-spawned sub-agents.

Flow per the spec:

1. *Purpose selection* — inline keyboard with exactly four options:
   Бизнес/работа, Модерация, Ролевая игра, Другое.
2. *Dynamic follow-up* — based on the chosen purpose, ask one targeted
   question (e.g. moderation → ask for filter rules).
3. *System prompt injection* — compile the answers into the persona that the
   sub-agent uses for the rest of its life.

State is persisted in the ``onboarding_state`` table so a sub-agent restart
mid-wizard does not lose progress.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..core.db import Database
from ..core.orchestrator import AgentOrchestrator, AgentRuntime


# Purpose ids must match the callback payloads emitted by the inline keyboard.
PURPOSES = {
    "business": {
        "label": "1. Бизнес, работа",
        "followup": (
            "Опиши вкратце, что за бизнес/направление и какой тон бот должен "
            "держать с клиентами (формальный / дружеский / экспертный). Можно "
            "одним сообщением, как тебе удобно."
        ),
        "persona_template": (
            "Ты — рабочий ассистент для бизнеса. Контекст: {answer}. "
            "Отвечай по делу, держи указанный тон, не выдумывай факты. "
            "Если спрашивают про услугу/цену/контакт, которой ты не знаешь — "
            "честно говори что уточнишь у владельца. Никогда не отправляй "
            "медиа, генерация изображений и видео тебе запрещена."
        ),
    },
    "moderation": {
        "label": "2. Модерация",
        "followup": (
            "Укажи правила фильтрации мата или спама — что именно карать и "
            "как (предупреждение → мут → бан). Можно списком или свободным "
            "текстом."
        ),
        "persona_template": (
            "Ты — модератор Telegram-чата. Правила: {answer}. "
            "Действуй строго по правилам, не делай исключений. "
            "При нарушении кратко объясняй причину санкции. "
            "Генерация изображений и видео запрещена."
        ),
    },
    "roleplay": {
        "label": "3. Ролевая игра",
        "followup": (
            "Опиши персонажа: имя, характер, манера речи, мир. Чем подробнее "
            "— тем убедительнее получится отыгрыш."
        ),
        "persona_template": (
            "Ты — персонаж по описанию: {answer}. "
            "Полностью оставайся в образе, говори от первого лица, не "
            "выходи из роли даже если попросят. Однако: запрещены сцены "
            "сексуального насилия, действий с несовершеннолетними и любая "
            "генерация медиа."
        ),
    },
    "other": {
        "label": "4. Другое",
        "followup": (
            "Опиши свободно, кем должен быть бот и что он должен делать. "
            "Я скомпилирую это в системный промпт."
        ),
        "persona_template": (
            "Ты — Telegram-ассистент с такой задачей: {answer}. "
            "Будь полезным, не ври, не выдумывай факты, говори честно "
            "когда чего-то не знаешь. Генерация изображений и видео "
            "запрещена."
        ),
    },
}

CALLBACK_PREFIX = "onb:"


@dataclass
class OnboardingDispatcher:
    """Wires onboarding handlers onto a sub-agent application."""

    orchestrator: AgentOrchestrator
    db: Database

    def install(self, runtime: AgentRuntime) -> None:
        if runtime.is_master:
            return  # master has its own handlers
        app = runtime.application
        app.add_handler(CommandHandler("start", self._start))
        app.add_handler(CallbackQueryHandler(self._purpose, pattern=f"^{CALLBACK_PREFIX}"))
        # The follow-up handler matches plain text only while we're still in
        # the "followup" step for this user, so it does not steal regular chat
        # messages afterwards.
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._maybe_followup),
            group=-1,
        )

    # ------------------------------------------------------------------
    # /start

    async def _start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        record = context.bot_data.get("agent_record")
        if record is None:
            await update.message.reply_text(
                "Этот бот ещё не привязан к платформе. Попроси владельца "
                "запустить /newagent в мастер-боте."
            )
            return

        if record.status == "running" and record.system_prompt:
            await update.message.reply_text(
                f"Бот «{record.display_name}» уже настроен. Просто пиши — отвечу."
            )
            return

        await self._save_state(record.id, "purpose", {})
        await update.message.reply_text(
            "Добро пожаловать! Я ещё не знаю, кем работать. Выбери одно из:",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton(p["label"], callback_data=f"{CALLBACK_PREFIX}{key}")]
                    for key, p in PURPOSES.items()
                ]
            ),
        )

    # ------------------------------------------------------------------
    # Inline keyboard → step 2

    async def _purpose(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()

        record = context.bot_data.get("agent_record")
        if record is None:
            await query.edit_message_text("Не могу определить, какой я бот.")
            return

        purpose_key = (query.data or "")[len(CALLBACK_PREFIX):]
        purpose = PURPOSES.get(purpose_key)
        if purpose is None:
            await query.edit_message_text("Не понял выбор. Запусти /start ещё раз.")
            return

        await self._save_state(record.id, "followup", {"purpose": purpose_key})
        await query.edit_message_text(
            f"Выбрано: {purpose['label']}.\n\n{purpose['followup']}"
        )

    # ------------------------------------------------------------------
    # Free-form follow-up → step 3

    async def _maybe_followup(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        record = context.bot_data.get("agent_record")
        if record is None:
            return

        state = await self._load_state(record.id)
        if state is None or state["step"] != "followup":
            return  # not in the middle of onboarding; let other handlers reply

        answer = update.message.text.strip()
        if not answer:
            return

        answers = state["answers"]
        answers["followup"] = answer
        purpose_key = answers.get("purpose", "other")
        purpose = PURPOSES.get(purpose_key, PURPOSES["other"])
        system_prompt = purpose["persona_template"].format(answer=answer)

        await self.orchestrator.update_persona(
            record.id,
            system_prompt=system_prompt,
            persona={"purpose": purpose_key, "answers": answers},
        )
        await self._save_state(record.id, "done", answers)

        # Refresh the bot_data record so subsequent messages see the new prompt.
        refreshed = await self.orchestrator.get_record(record.id)
        if refreshed is not None:
            context.bot_data["agent_record"] = refreshed

        await update.message.reply_text(
            "Готово! Я настроен. Теперь просто пиши — отвечу в выбранной роли. "
            "Если что-то нужно перенастроить — попроси владельца запустить "
            "/reconfigure для этого бота в мастер-чате."
        )

    # ------------------------------------------------------------------
    # State helpers

    async def _save_state(self, agent_id: int, step: str, answers: dict) -> None:
        await self.db.execute(
            "INSERT INTO onboarding_state(owner_id, agent_id, step, answers_json) "
            "VALUES((SELECT owner_id FROM sub_agents WHERE id = ?), ?, ?, ?) "
            "ON CONFLICT(owner_id, agent_id) DO UPDATE SET "
            "step = excluded.step, answers_json = excluded.answers_json, "
            "updated_at = datetime('now')",
            (agent_id, agent_id, step, json.dumps(answers, ensure_ascii=False)),
        )

    async def _load_state(self, agent_id: int) -> dict | None:
        row = await self.db.fetchone(
            "SELECT step, answers_json FROM onboarding_state WHERE agent_id = ?",
            (agent_id,),
        )
        if row is None:
            return None
        try:
            answers = json.loads(row["answers_json"])
        except json.JSONDecodeError:
            answers = {}
        return {"step": row["step"], "answers": answers}

    async def reset(self, agent_id: int) -> None:
        await self.db.execute(
            "DELETE FROM onboarding_state WHERE agent_id = ?", (agent_id,)
        )
