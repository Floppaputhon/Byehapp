"""AI client for vectorengine.ai (OpenAI-compatible API)."""

import re as _re

import aiohttp

from config import AI_API_KEY, AI_API_URL, AI_MODEL


async def ai_chat(
    system_prompt: str,
    user_message: str,
    max_tokens: int = 1000,
    history: list[dict] | None = None,
) -> str:
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
    }
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})
    payload = {
        "model": AI_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{AI_API_URL}/chat/completions",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"]
                error_text = await resp.text()
                return f"Ошибка AI API ({resp.status}): {error_text}"
    except Exception as e:
        return f"Ошибка подключения к AI: {e}"


async def summarize_messages(messages: list[dict]) -> str:
    if not messages:
        return "Нет сообщений для анализа."

    formatted = []
    for msg in messages:
        name = msg.get("first_name") or "Неизвестный"
        text = (
            msg.get("text")
            or msg.get("caption")
            or f"[{msg.get('media_type', 'медиа')}]"
        )
        formatted.append(f"{name}: {text}")

    conversation = "\n".join(formatted[-100:])

    system_prompt = (
        "Ты — умный ассистент. Сделай краткий пересказ переписки на русском языке. "
        "Укажи: кто писал, основные темы, важные решения, вопросы без ответа. "
        "Будь кратким но информативным. Используй структурированный формат."
    )

    return await ai_chat(system_prompt, f"Вот переписка:\n\n{conversation}")


async def translate_text(text: str, target_lang: str = "русский") -> str:
    system_prompt = (
        f"Переведи текст на {target_lang}. Отвечай только переводом, без пояснений."
    )
    return await ai_chat(system_prompt, text, max_tokens=500)


async def analyze_message(text: str) -> str:
    system_prompt = (
        "Проанализируй сообщение. Определи:\n"
        "1. Тон и настроение\n"
        "2. Требуется ли ответ\n"
        "3. Срочность (низкая/средняя/высокая)\n"
        "4. Краткое резюме\n"
        "Отвечай кратко на русском."
    )
    return await ai_chat(system_prompt, text, max_tokens=300)


_DIALOG_PATTERNS = _re.compile(
    r"(?:что\s+(?:происходит|случилось|нового|там|пишут|было)|"
    r"кто\s+(?:писал|написал|пишет|отвечал)|"
    r"(?:пересказ|перескажи|расскажи\s+(?:что|кто))|"
    r"(?:что\s+в\s+(?:чат|груп))|"
    r"(?:последние|новые)\s+сообщени|"
    r"(?:что\s+обсуждал|о\s+чём\s+(?:говорил|писал)))",
    _re.IGNORECASE,
)

_SCHEDULE_PATTERNS = _re.compile(
    r"(?:(?:напиши|отправь|пошли|скажи|написать|отправить)\s+.*?"
    r"(?:через|в\s+\d|позже|потом|завтра|вечером|утром|ночью|запланируй)|"
    r"запланируй|"
    r"(?:через\s+\d+\s*(?:мин|час|ч\b|м\b))\s*.*?"
    r"(?:напиши|отправь|пошли|скажи|написать|отправить))",
    _re.IGNORECASE,
)

_SEND_PATTERNS = _re.compile(
    r"(?:^(?:напиши|пиши|отправь|пошли|скажи|написать|отправить)\s+\S|"
    r"(?:напиши|пиши|отправь|пошли)\s+.*?(?:ему|ей|им|туда|в\s+чат|@\w))",
    _re.IGNORECASE,
)


def classify_intent(message: str) -> str:
    text = message.strip()
    if _DIALOG_PATTERNS.search(text):
        return "DIALOG_READ"
    if _SCHEDULE_PATTERNS.search(text):
        return "SCHEDULE_MESSAGE"
    if _SEND_PATTERNS.search(text):
        return "MESSAGE_SEND"
    return "GENERAL"


async def answer_question(
    question: str,
    history: list[dict] | None = None,
) -> str:
    system_prompt = (
        "Ты — умный AI-ассистент в Telegram по имени gemeni. "
        "Ты помнишь весь диалог с пользователем. "
        "Отвечай на вопросы точно, полезно и кратко. "
        "Отвечай на том языке, на котором задан вопрос. "
        "Если вопрос на русском — отвечай на русском."
    )
    return await ai_chat(system_prompt, question, max_tokens=1500, history=history)


async def answer_with_dialog(question: str, messages: list[dict]) -> str:
    formatted = []
    for msg in messages:
        name = msg.get("first_name") or "Неизвестный"
        text = (
            msg.get("text")
            or msg.get("caption")
            or f"[{msg.get('media_type', 'медиа')}]"
        )
        date_str = (msg.get("date") or "")[:16].replace("T", " ")
        formatted.append(f"[{date_str}] {name}: {text}")

    conversation = "\n".join(formatted[-100:])

    system_prompt = (
        "Ты — умный AI-ассистент в Telegram с доступом к истории чатов. "
        "Пользователь спрашивает о том, что происходит в его чатах. "
        "Используй предоставленную историю сообщений чтобы ответить. "
        "Отвечай кратко и информативно на русском."
    )
    user_msg = f"Вопрос: {question}\n\nИстория сообщений:\n{conversation}"
    return await ai_chat(system_prompt, user_msg, max_tokens=1500)


async def compose_message(request: str) -> str:
    system_prompt = (
        "Пользователь просит тебя написать сообщение для отправки в Telegram. "
        "Напиши ТОЛЬКО текст сообщения, без пояснений и кавычек. "
        "Пиши естественно, как обычный человек в мессенджере."
    )
    return await ai_chat(system_prompt, request, max_tokens=500)
