"""AI client for vectorengine.ai (OpenAI-compatible API)."""

import base64 as _b64
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
    r"(?:можешь|можете)\s+(?:написать|отправить|послать)|"
    r"(?:напиши|пиши|отправь|пошли|написать|отправить)\s+.*?(?:ему|ей|им|туда|в\s+чат|@\w)|"
    r"(?:напиши|отправь|написать|отправить)\s+сообщение)",
    _re.IGNORECASE,
)

_BROADCAST_PATTERNS = _re.compile(
    r"(?:(?:напиши|отправь|пошли|написать|отправить)\s+всем|"
    r"рассылк[аиу]|"
    r"(?:массов|всем\s+(?:напиши|отправь|пошли)))",
    _re.IGNORECASE,
)

_NOTE_PATTERNS = _re.compile(
    r"(?:запомни|запиши|заметк[аиу]|сохрани\s+(?:заметку|запись)|"
    r"(?:мои|покажи|список)\s+заметк|что\s+(?:я\s+)?записывал|"
    r"удали\s+заметк)",
    _re.IGNORECASE,
)

_CONTACT_PATTERNS = _re.compile(
    r"(?:(?:расскажи|инфо|информаци)\s+(?:про|о|об)\s+@\w|"
    r"кто\s+тако[йе]\s+@\w|"
    r"стати?стик[аиу]\s+@\w|"
    r"@\w+\s+(?:кто|инфо|стат))",
    _re.IGNORECASE,
)

_AUTOREPLY_PATTERNS = _re.compile(
    r"(?:(?:включи|выключи|убери|отключи|установи|поставь|отключить|включить)\s+автоответ|"
    r"автоответ\s*(?:вкл|выкл|on|off|статус|\?)|"
    r"автоответчик)",
    _re.IGNORECASE,
)

_QR_PATTERNS = _re.compile(
    r"(?:быстры[йе]\s+ответ|шаблон\s+ответ|"
    r"сохрани\s+(?:быстрый\s+)?ответ|"
    r"(?:мои|покажи|список)\s+(?:шаблон|быстр))",
    _re.IGNORECASE,
)


_DELETED_PATTERNS = _re.compile(
    r"(?:(?:покажи|показать)\s+удалённые|"
    r"удалённые\s+сообщени|"
    r"что\s+удалил[аиоы]?|кто\s+удалил)",
    _re.IGNORECASE,
)

_PENDING_PATTERNS = _re.compile(
    r"(?:кому\s+(?:надо|нужно|должен)?\s*(?:ответить|написать|позвонить)|"
    r"неотвеченн|ожидают\s+ответ|непрочитанн)",
    _re.IGNORECASE,
)

_STATS_PATTERNS = _re.compile(
    r"(?:(?:покажи|показать)?\s*стати[сц]тик|сколько\s+сообщен)",
    _re.IGNORECASE,
)

_SEARCH_PATTERNS = _re.compile(
    r"(?:(?:найди|ищи|поиск)\s+(?:сообщени|в\s+чат)|поиск\s+по\s+сообщени)",
    _re.IGNORECASE,
)

_EXPORT_PATTERNS = _re.compile(
    r"(?:экспорт(?:ируй)?\s+(?:чат|истори)|"
    r"выгрузи\s+(?:чат|истори)|скачай\s+(?:чат|истори))",
    _re.IGNORECASE,
)

_REMIND_LIST_PATTERNS = _re.compile(
    r"(?:(?:мои|покажи|список|активные)\s+напоминани|напоминани[яе]\s+(?:мои|список|покажи))",
    _re.IGNORECASE,
)

_REMIND_SET_PATTERNS = _re.compile(
    r"(?:напомни\s+(?:мне\s+)?(?:через|в\s+\d)|поставь\s+напоминани|установи\s+напоминани)",
    _re.IGNORECASE,
)

_SUMMARY_PATTERNS = _re.compile(
    r"(?:(?:сделай|дай)\s+пересказ|перескажи\s+чат|(?:сводка|пересказ)\s+(?:за|чат))",
    _re.IGNORECASE,
)

_PRIORITY_PATTERNS = _re.compile(
    r"(?:срочн|важност|приоритет|(?:кому|что)\s+(?:первым?|срочн|важн)\s+(?:ответить|написать)|"
    r"рейтинг\s+(?:чатов|ответов)|топ\s+(?:чатов|ответов)|сортируй\s+чаты)",
    _re.IGNORECASE,
)


def classify_intent(message: str) -> str:
    text = message.strip()
    if _DIALOG_PATTERNS.search(text):
        return "DIALOG_READ"
    if _AUTOREPLY_PATTERNS.search(text):
        return "AUTOREPLY"
    if _NOTE_PATTERNS.search(text):
        return "NOTE"
    if _BROADCAST_PATTERNS.search(text):
        return "BROADCAST"
    if _CONTACT_PATTERNS.search(text):
        return "CONTACT_INFO"
    if _QR_PATTERNS.search(text):
        return "QUICK_REPLY"
    if _DELETED_PATTERNS.search(text):
        return "DELETED"
    if _PENDING_PATTERNS.search(text):
        return "PENDING"
    if _STATS_PATTERNS.search(text):
        return "STATS"
    if _REMIND_LIST_PATTERNS.search(text):
        return "REMIND_LIST"
    if _REMIND_SET_PATTERNS.search(text):
        return "REMIND_SET"
    if _SEARCH_PATTERNS.search(text):
        return "SEARCH"
    if _EXPORT_PATTERNS.search(text):
        return "EXPORT"
    if _SUMMARY_PATTERNS.search(text):
        return "SUMMARY"
    if _PRIORITY_PATTERNS.search(text):
        return "PRIORITY"
    if _SCHEDULE_PATTERNS.search(text):
        return "SCHEDULE_MESSAGE"
    if _SEND_PATTERNS.search(text):
        return "MESSAGE_SEND"
    return "GENERAL"


async def transcribe_voice(file_path: str) -> str:
    """Transcribe a voice file using OpenAI-compatible whisper API."""
    headers = {"Authorization": f"Bearer {AI_API_KEY}"}
    fh = None
    try:
        fh = open(file_path, "rb")  # noqa: SIM115
        data = aiohttp.FormData()
        data.add_field(
            "file",
            fh,
            filename="voice.ogg",
            content_type="audio/ogg",
        )
        data.add_field("model", "whisper-1")
        data.add_field("language", "ru")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{AI_API_URL}/audio/transcriptions",
                headers=headers,
                data=data,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    text = result.get("text", "")
                    if text:
                        return text
    except Exception:
        pass
    finally:
        if fh:
            fh.close()

    return await _google_transcribe(file_path)


async def _google_transcribe(ogg_path: str) -> str:
    """Fallback: convert OGG to WAV via ffmpeg, then use Google free speech API."""
    import asyncio
    import os
    import subprocess as _sp

    wav_path = ogg_path + ".wav"
    try:
        proc = await asyncio.to_thread(
            _sp.run,
            ["ffmpeg", "-y", "-i", ogg_path, "-ar", "16000", "-ac", "1", wav_path],
            capture_output=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return ""
    except Exception:
        return ""

    if not os.path.exists(wav_path):
        return ""

    try:
        import speech_recognition as sr

        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio = recognizer.record(source)
        text = await asyncio.to_thread(
            recognizer.recognize_google, audio, language="ru-RU"
        )
        return text
    except Exception:
        return ""
    finally:
        try:
            os.remove(wav_path)
        except Exception:
            pass


async def answer_question(
    question: str,
    history: list[dict] | None = None,
) -> str:
    system_prompt = (
        "Ты — gemeni, AI-помощник в Telegram. "
        "Ты подключён как бизнес-бот через Telegram Business API. "
        "Ты МОЖЕШЬ: отправлять сообщения, читать чаты, планировать отправку, "
        "показывать удалённые сообщения, пересказывать чаты, ставить напоминания, "
        "показывать кому ответить, статистику, поиск, перевод, анализ тона, "
        "включать/выключать автоответ, сохранять заметки, делать рассылку, "
        "показывать инфо о контакте, экспортировать чаты, "
        "управлять шаблонами ответов, расшифровывать голосовые. "
        "Ты помнишь весь диалог с пользователем.\n\n"
        "ВАЖНО — стиль общения:\n"
        "- Пиши как обычный живой человек в чате, НЕ как робот\n"
        "- Используй разговорный стиль: 'ну', 'короче', 'кста', 'ага', 'хз', 'ок' и т.д.\n"
        "- Можно использовать сокращения: 'чё', 'щас', 'норм', 'прям', 'оч'\n"
        "- НЕ начинай каждое сообщение с 'Конечно!' или 'Отлично!'\n"
        "- Отвечай коротко где можно, без воды и формальностей\n"
        "- Если вопрос простой — ответ в 1-2 предложения\n"
        "- Используй эмодзи умеренно, как обычный человек\n"
        "- НЕ используй списки и пункты без необходимости\n"
        "- Говори на том же языке что и собеседник\n"
        "- Можешь шутить, быть саркастичным, использовать мемы если уместно\n"
        "- Пиши строчными буквами, не КАПСОМ (кроме выделения)\n"
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


async def rank_chat_priority(chats: list[dict]) -> str:
    """Use AI to rank pending chats by urgency."""
    if not chats:
        return "Нет неотвеченных чатов!"

    formatted = []
    for chat in chats:
        name = chat.get("first_name") or "Неизвестный"
        username = f" (@{chat['username']})" if chat.get("username") else ""
        msgs = chat.get("recent_messages", [])
        msg_lines = []
        for m in msgs:
            text = m.get("text") or m.get("caption") or ""
            date_str = (m.get("date") or "")[:16].replace("T", " ")
            sender = m.get("first_name") or "?"
            if text:
                msg_lines.append(f"  [{date_str}] {sender}: {text[:120]}")
        context = "\n".join(msg_lines) if msg_lines else "  (нет контекста)"
        formatted.append(f"{name}{username}:\n{context}")

    conversations = "\n\n".join(formatted)

    system_prompt = (
        "Ты — AI-ассистент, который анализирует чаты в Telegram. "
        "Тебе даны неотвеченные чаты с последними сообщениями. "
        "Определи приоритет ответа для каждого чата: "
        "🔴 Срочно — нужен ответ немедленно (вопросы, просьбы, жалобы, дедлайны). "
        "🟡 Важно — стоит ответить скоро (обсуждения, предложения). "
        "🟢 Не срочно — можно ответить позже (болтовня, мемы, спам). "
        "Составь топ-лист от самого срочного к менее срочному. "
        "Для каждого чата укажи: приоритет, имя, и КОРОТКУЮ причину (1 предложение). "
        "Формат:\n🔴 Имя — причина\n🟡 Имя — причина\n🟢 Имя — причина"
    )
    return await ai_chat(system_prompt, conversations, max_tokens=1500)


async def describe_image(image_path: str, question: str = "") -> str:
    """Describe an image using AI vision API."""
    try:
        with open(image_path, "rb") as f:
            img_data = _b64.b64encode(f.read()).decode()
    except Exception as e:
        return f"Ошибка чтения файла: {e}"

    user_text = question or "Опиши это изображение подробно."
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": (
                "Ты — AI-ассистент, который описывает изображения. "
                "Отвечай подробно и полезно на русском."
            )},
            {"role": "user", "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{img_data}",
                }},
            ]},
        ],
        "max_tokens": 1000,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{AI_API_URL}/chat/completions",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=45),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"]
                error_text = await resp.text()
                return f"Ошибка AI Vision ({resp.status}): {error_text[:200]}"
    except Exception as e:
        return f"Ошибка подключения к AI: {e}"


async def compose_message(request: str) -> str:
    system_prompt = (
        "Пользователь просит отправить сообщение в Telegram. "
        "Извлеки из его запроса ТОЧНЫЙ текст сообщения для отправки. "
        "Если пользователь указал конкретный текст — верни его ДОСЛОВНО. "
        "НЕ придумывай текст от себя. НЕ добавляй приветствия. "
        "Верни ТОЛЬКО текст сообщения, без кавычек и пояснений."
    )
    return await ai_chat(system_prompt, request, max_tokens=500)
