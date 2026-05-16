"""AI client for vectorengine.ai (OpenAI-compatible API)."""

import aiohttp

from config import AI_API_KEY, AI_API_URL, AI_MODEL


async def ai_chat(
    system_prompt: str, user_message: str, max_tokens: int = 1000
) -> str:
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
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


async def classify_intent(message: str) -> str:
    system_prompt = (
        "Классифицируй намерение пользователя. Ответь ОДНИМ словом:\n"
        "- DIALOG_READ — пользователь спрашивает что происходит в чате/группе, "
        "кто писал, что обсуждали, просит пересказ\n"
        "- MESSAGE_SEND — пользователь просит написать/отправить сообщение кому-то прямо сейчас\n"
        "- SCHEDULE_MESSAGE — пользователь просит написать/отправить сообщение позже, "
        "в определённое время, запланировать\n"
        "- GENERAL — обычный вопрос, не связанный с чатами/отправкой\n\n"
        "Ответь ТОЛЬКО одним словом: DIALOG_READ, MESSAGE_SEND, SCHEDULE_MESSAGE или GENERAL"
    )
    result = await ai_chat(system_prompt, message, max_tokens=20)
    result = result.strip().upper()
    for intent in ("DIALOG_READ", "MESSAGE_SEND", "SCHEDULE_MESSAGE", "GENERAL"):
        if intent in result:
            return intent
    return "GENERAL"


async def answer_question(question: str, context: str = "") -> str:
    system_prompt = (
        "Ты — умный AI-ассистент в Telegram. Отвечай на вопросы пользователя "
        "точно, полезно и кратко. Отвечай на том языке, на котором задан вопрос. "
        "Если вопрос на русском — отвечай на русском."
    )
    user_msg = question
    if context:
        user_msg = f"Контекст диалога:\n{context}\n\nВопрос: {question}"
    return await ai_chat(system_prompt, user_msg, max_tokens=1500)


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
