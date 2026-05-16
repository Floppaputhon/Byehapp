import json
import logging
import tempfile
from pathlib import Path

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

REPLY_SYSTEM_PROMPT = """You are {bot_name}, a helpful and friendly AI assistant for a Telegram Business account.
You provide high-quality, contextual replies based on the conversation history.
Be concise, helpful, and professional. Reply in the same language as the user's message.
If the user writes in Russian, reply in Russian. If in English, reply in English.
Never break character. Your name is {bot_name}."""

GROUP_REPLY_PROMPT = """You are {bot_name}, a helpful AI assistant in a Telegram group chat.
Someone mentioned you or replied to your message. Respond helpfully and concisely.
Reply in the same language as the user. Keep responses short for group chats.
Your name is {bot_name}."""

TRANSLATION_PROMPT = """Translate the following text to {target_lang}. Return ONLY the translation, nothing else."""

SUMMARY_PROMPT = """Summarize the following conversation concisely in the same language as the messages. Highlight key points."""


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


async def generate_reply(
    chat_history: list[dict[str, str]],
    new_message: str,
    bot_name: str = "AI Assistant",
    is_group: bool = False,
) -> str:
    prompt = GROUP_REPLY_PROMPT if is_group else REPLY_SYSTEM_PROMPT
    system_content = prompt.format(bot_name=bot_name)
    messages = [{"role": "system", "content": system_content}]
    messages.extend(chat_history)
    messages.append({"role": "user", "content": new_message})
    return await _call_llm(messages, max_tokens=1024)


async def translate_text(text: str, target_lang: str) -> str:
    messages = [
        {"role": "system", "content": TRANSLATION_PROMPT.format(target_lang=target_lang)},
        {"role": "user", "content": text},
    ]
    return await _call_llm(messages, max_tokens=1024)


async def summarize_chat(chat_history: list[dict[str, str]]) -> str:
    history_text = "\n".join(
        f"{m['role']}: {m['content']}" for m in chat_history
    )
    messages = [
        {"role": "system", "content": SUMMARY_PROMPT},
        {"role": "user", "content": history_text},
    ]
    return await _call_llm(messages, max_tokens=512)


async def transcribe_voice(file_bytes: bytes, file_ext: str = ".ogg") -> str:
    tmp_path = Path(tempfile.mktemp(suffix=file_ext))
    try:
        tmp_path.write_bytes(file_bytes)
        with open(tmp_path, "rb") as audio_file:
            transcript = await client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="ru",
            )
        return transcript.text
    except Exception as e:
        logger.warning("Transcription failed: %s", e)
        return ""
    finally:
        tmp_path.unlink(missing_ok=True)
