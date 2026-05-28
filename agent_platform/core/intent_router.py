"""Language-agnostic intent routing.

Per the spec, complex actions (especially integration connections) must NOT
be dispatched via hard-coded regex or strict command parsing. Instead, every
free-form user message goes through the configured LLM with the platform's
tool schemas attached; the model picks the right tool and arguments — be it
Russian, English, German, slang, or typos.

The classifier is intentionally separate from the chat-style assistant: it
runs with ``tool_choice="auto"`` and is asked to **only** call a tool if the
user's intent clearly matches one. If it just wants to chat, the model
returns plain text and we forward that as a normal assistant reply.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from ..providers import ChatMessage, ToolSpec
from ..providers.base import ChatResult
from .ai_router import AIRouter

logger = logging.getLogger(__name__)


INTENT_SYSTEM_PROMPT = (
    "You are an intent router for a multi-bot Telegram assistant. "
    "Given the user's message in ANY language (Russian, English, German, "
    "Ukrainian, Spanish, slang, typos — all of it), decide whether the user "
    "is trying to invoke one of the platform tools below or just wants to "
    "chat. If the user's intent clearly maps to a tool, call exactly one "
    "tool with the best arguments you can infer. If the user is just "
    "chatting or the request is ambiguous, respond with plain text instead "
    "and do not call any tool. Examples of phrases that all mean 'connect "
    "to GitHub': 'connect github', 'законнектись к гитхабу', 'подруби "
    "гидхаб пожалуйста', 'mach verbindung mit github', 'привяжи мой гит'."
)


@dataclass
class IntentDecision:
    """Outcome of running the intent router on a user message."""

    tool_name: Optional[str]
    arguments: dict
    fallback_text: str
    raw: Optional[ChatResult] = None

    @property
    def matched_tool(self) -> bool:
        return self.tool_name is not None


class IntentRouter:
    def __init__(self, ai: AIRouter) -> None:
        self._ai = ai

    async def classify(
        self,
        *,
        owner_id: int,
        user_text: str,
        tools: list[ToolSpec],
        chat_history: Optional[list[ChatMessage]] = None,
        prefer_provider: Optional[str] = None,
    ) -> IntentDecision:
        if not tools:
            return IntentDecision(
                tool_name=None,
                arguments={},
                fallback_text="",
            )

        messages: list[ChatMessage] = []
        if chat_history:
            messages.extend(chat_history)
        messages.append(ChatMessage(role="user", content=user_text))

        try:
            result = await self._ai.chat(
                owner_id=owner_id,
                system=INTENT_SYSTEM_PROMPT,
                messages=messages,
                tools=tools,
                require_tools=True,
                prefer_provider=prefer_provider,
                temperature=0.0,
                max_tokens=512,
            )
        except Exception:
            logger.exception("Intent router failed; falling back to chat")
            return IntentDecision(
                tool_name=None,
                arguments={},
                fallback_text="",
            )

        if result.tool_calls:
            call = result.tool_calls[0]
            return IntentDecision(
                tool_name=call.name,
                arguments=call.arguments or {},
                fallback_text=result.text,
                raw=result,
            )
        return IntentDecision(
            tool_name=None,
            arguments={},
            fallback_text=result.text,
            raw=result,
        )
