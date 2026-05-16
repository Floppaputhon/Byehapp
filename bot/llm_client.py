import json
import logging

from openai import AsyncOpenAI

from bot.config import VECTORENGINE_API_KEY, VECTORENGINE_BASE_URL, LLM_MODEL, LLM_MODEL_FALLBACK

logger = logging.getLogger(__name__)

client = AsyncOpenAI(
    api_key=VECTORENGINE_API_KEY,
    base_url=VECTORENGINE_BASE_URL,
)

MODERATION_SYSTEM_PROMPT = """You are a strict chat moderator. Analyze the following message and determine if it should be blocked.

Block messages that contain:
- Spam or advertising
- Unwanted/suspicious links
- Aggressive, hateful, or abusive language
- Scam attempts

Respond ONLY with a JSON object (no markdown, no code fences):
{"block": true/false, "reason": "brief explanation"}

If the message is normal and safe, respond: {"block": false, "reason": "ok"}"""

REPLY_SYSTEM_PROMPT = """You are a helpful and friendly AI assistant for a Telegram Business account. 
You provide high-quality, contextual replies based on the conversation history.
Be concise, helpful, and professional. Reply in the same language as the user's message.
If the user writes in Russian, reply in Russian. If in English, reply in English."""


async def _call_llm(messages: list[dict[str, str]], max_tokens: int = 1024) -> str:
    models = [LLM_MODEL, LLM_MODEL_FALLBACK]
    for model in models:
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.3,
            )
            content = response.choices[0].message.content
            return content if content else ""
        except Exception as e:
            logger.warning("Model %s failed: %s", model, e)
            continue
    logger.error("All models failed")
    return ""


async def moderate_message(text: str) -> dict[str, object]:
    messages = [
        {"role": "system", "content": MODERATION_SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
    raw = await _call_llm(messages, max_tokens=150)

    try:
        result = json.loads(raw)
        return {"block": bool(result.get("block", False)), "reason": str(result.get("reason", ""))}
    except (json.JSONDecodeError, KeyError):
        logger.warning("Failed to parse moderation response: %s", raw)
        return {"block": False, "reason": "parse_error"}


async def generate_reply(chat_history: list[dict[str, str]], new_message: str) -> str:
    messages = [{"role": "system", "content": REPLY_SYSTEM_PROMPT}]
    messages.extend(chat_history)
    messages.append({"role": "user", "content": new_message})

    return await _call_llm(messages, max_tokens=1024)
