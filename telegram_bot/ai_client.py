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
        "Сделай краткий пересказ переписки на русском. "
        "Укажи: кто писал, основные темы, важные решения, вопросы без ответа. "
        "Будь кратким но информативным.\n" + _HUMAN_STYLE
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


_BOT_SELF_KNOWLEDGE = (
    "ПОЛНАЯ ИНФОРМАЦИЯ О ТЕБЕ И ТВОИХ ВОЗМОЖНОСТЯХ:\n\n"
    "Ты — gemeni, AI бизнес-ассистент в Telegram, созданный для помощи владельцу.\n"
    "Ты подключён через Telegram Business API и можешь работать как в ЛС так и в группах.\n\n"
    "=== КОМАНДЫ ДЛЯ ЛИЧНЫХ СООБЩЕНИЙ (ЛС / Business API) ===\n"
    "/deleted [кол-во] — показать удалённые сообщения (текст, фото, видео, голосовые)\n"
    "/summary [часы] — AI-пересказ переписки за N часов\n"
    "/remind [кому] [что] [когда] — поставить напоминание (30м, 2ч, 18:00, 3д)\n"
    "/myreminders — список активных напоминаний\n"
    "/pending — кому нужно ответить (неотвеченные)\n"
    "/priority — приоритетный рейтинг чатов (срочно/важно/не срочно)\n"
    "/stats [часы] — статистика сообщений\n"
    "/search [запрос] — поиск по сообщениям\n"
    "/translate [текст] — перевод текста\n"
    "/analyze — анализ тона сообщения (ответом)\n"
    "/autoreply on [текст] | off — автоответ на входящие (1 раз на чат)\n"
    "/note save [текст] | list | search [запрос] | delete [id] — заметки\n"
    "/broadcast [текст] — рассылка всем контактам\n"
    "/contact @username — инфо о контакте (имя, ID, статистика)\n"
    "/export [@username] — экспорт истории чата\n"
    "/qr save [имя] [текст] | list | use [имя] — шаблоны быстрых ответов\n"
    "/chats — список всех приватных чатов (ЛС)\n"
    "/groups — список всех групп бота\n"
    "/whoami — твой Telegram ID, имя, username\n"
    "/access [owner|admins|all] — настройка кто может использовать бота в группе\n\n"
    "=== КОМАНДЫ ДЛЯ ГРУПП ===\n"
    "/groupstats [часы] — статистика группы\n"
    "/groupsummary [часы] — AI-пересказ группы\n"
    "/top — топ участников группы по сообщениям\n"
    "/groupsearch [запрос] — поиск по сообщениям группы\n"
    "В группе — упомяни меня @bot или ответь на моё сообщение для AI-ответа\n\n"
    "=== МОДЕРАЦИЯ ГРУПП ===\n"
    "/moder on|off — включить/выключить модерацию\n"
    "/moder welcome [текст] — приветствие новых участников ({name} = имя)\n"
    "/moder badwords [слова,через,запятую] — фильтр мата\n"
    "/moder antiflood [макс] [сек] — антифлуд\n"
    "/rules [текст] — правила группы\n"
    "/warn [период] [причина] — предупредить (3 варна = кик)\n"
    "/unwarn — снять последнее предупреждение\n"
    "/warnlist — список предупреждений\n"
    "/warnlimit [число] — лимит варнов (1-20)\n"
    "/mute [период] [причина] — замутить\n"
    "/unmute — размутить\n"
    "/kick [причина] — кикнуть\n"
    "/ban [период] [причина] — забанить\n"
    "/unban — разбанить\n"
    "/pin — закрепить сообщение\n"
    "/unpin — открепить все\n"
    "/poll Вопрос | вариант1 | вариант2 — голосование\n"
    "/report — пожаловаться админам\n"
    "/whoadmin — список админов\n\n"
    "=== ИРИС-СТИЛЬ КОМАНДЫ (текстовые, без /) ===\n"
    "Пишутся прямо текстом в группе, БЕЗ префиксов (но !, ., Ирис тоже работают):\n"
    "Примеры: варн спам, бан 2 дня реклама, кик, мут 1 час\n"
    "варн, -варн, варнлист, мои варны, варн лимит N\n"
    "мут/заткни, -мут/размут, муты\n"
    "бан/чс, -бан/разбан, банлист\n"
    "кик, кто админ, позвать админов, модер лог\n\n"
    "=== ТЕКСТОВЫЕ КОМАНДЫ (без /) ===\n"
    "Бот понимает естественный язык:\n"
    "- 'Срочно!' / 'приоритет' → приоритет чатов\n"
    "- 'Запомни купить молоко' → заметка\n"
    "- 'Напиши всем привет' → рассылка\n"
    "- 'Расскажи про @ivan' → инфо о контакте\n"
    "- 'Включи автоответчик' → автоответ\n"
    "- 'Покажи удалённые' → удалённые сообщения\n"
    "- 'Кому ответить' → неотвеченные\n"
    "- 'Статистика' → статистика\n"
    "- 'Напомни через 30 минут позвонить' → напоминание\n"
    "- 'Что происходит в чате?' → AI-пересказ с историей\n"
    "- 'Напиши @user привет' → отправка сообщения\n\n"
    "=== ДОПОЛНИТЕЛЬНЫЕ ВОЗМОЖНОСТИ ===\n"
    "- Распознавание фото (отправь фото — опишу что на нём)\n"
    "- Расшифровка голосовых сообщений + AI-ответ\n"
    "- Память последних 20 сообщений в каждом чате\n"
    "- Пароль для доступа (если настроен)\n"
    "- Автоматическое отслеживание удалённых сообщений через Business API\n"
    "- Периоды времени: 1 минута, 2 часа, 3 дня, 1 неделя, 2 месяца, 1 год\n"
)

_HUMAN_STYLE = (
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

_IRIS_KNOWLEDGE = (
    "ЗНАНИЯ ОБ ИРИС (Iris CM — бот-менеджер для модерации групп):\n"
    "Ты знаешь и умеешь пользоваться системой команд Ирис как настоящий модератор.\n\n"
    "КОМАНДЫ пишутся прямо текстом в группе, БЕЗ префиксов.\n"
    "(Префиксы !, ., /, Ирис, Ириска тоже работают, но необязательны)\n"
    "Пример: варн спам, бан 2 дня, кик, мут 1 час\n\n"
    "СИСТЕМА ПРЕДУПРЕЖДЕНИЙ (варны):\n"
    "- варн [период] [причина] — выдать предупреждение (ответом на сообщение)\n"
    "  Примеры: варн спам, варн 2 дня оскорбления, варн 1 час флуд\n"
    "- -варн — снять последнее предупреждение (ответом)\n"
    "- снять варны / снять все варны — снять все предупреждения\n"
    "- варнлист — показать последние предупреждения в чате\n"
    "- мои варны — показать свои предупреждения\n"
    "- варны @юзер — показать варны конкретного пользователя\n"
    "- варн лимит N — установить лимит варнов (1-20, по умолчанию 3)\n"
    "  При достижении лимита пользователь автоматически кикается\n"
    "  Варны могут истекать по времени (если указан период)\n\n"
    "СИСТЕМА МУТОВ:\n"
    "- мут [период] [причина] — замутить пользователя (ответом)\n"
    "  Примеры: мут 1 час, мут 30 минут флуд, заткни 2 часа\n"
    "  Синонимы: мут, заткнуть, заткни\n"
    "- -мут / размут / unmute — размутить (ответом)\n"
    "- муты — список замученных в чате\n\n"
    "СИСТЕМА БАНОВ:\n"
    "- бан [период] [причина] — забанить (ответом)\n"
    "  Примеры: бан, бан 1 неделя реклама, бан навсегда\n"
    "  Синонимы: бан, чс (чёрный список)\n"
    "- -бан / разбан / unban — разбанить (ответом)\n"
    "- банлист — список забаненных\n\n"
    "КИК:\n"
    "- кик — кикнуть из группы (ответом)\n\n"
    "ИНФОРМАЦИЯ О МОДЕРАЦИИ:\n"
    "- кто админ / а судьи кто / кто здесь власть — список админов\n"
    "- позвать админов / созвать модеров — позвать всех админов\n"
    "- модер лог — лог модераторских действий\n\n"
    "ПЕРИОДЫ ВРЕМЕНИ (парсинг):\n"
    "Формат: число + единица. Поддерживаются все склонения.\n"
    "- минуты: 1 минута, 5 минут, 30 мин, 10 м\n"
    "- часы: 1 час, 2 часа, 5 часов, 3 ч\n"
    "- дни: 1 день, 2 дня, 7 дней, 3 суток\n"
    "- недели: 1 неделя, 2 недели, нед\n"
    "- месяцы: 1 месяц, 3 месяца, 6 месяцев\n"
    "- годы: 1 год, 2 года, 5 лет\n\n"
    "УПРАВЛЕНИЕ МОДЕРАЦИЕЙ:\n"
    "- /moder on — включить модерацию (антифлуд, фильтр мата, приветствие)\n"
    "- /moder off — выключить\n"
    "- /rules текст — установить правила группы\n"
    "- /rules — показать правила\n\n"
    "Ты можешь объяснить любую команду Ирис, подсказать как модерировать, "
    "и выполнять модераторские действия. Ты — опытный модератор, который "
    "знает все тонкости работы с Ирис.\n"
)


async def answer_question(
    question: str,
    history: list[dict] | None = None,
    is_group: bool = False,
) -> str:
    if is_group:
        system_prompt = (
            "Ты — gemeni, AI-помощник в групповом чате Telegram. "
            "Сейчас ты находишься В ГРУППЕ, а НЕ в личных сообщениях. "
            "В группе ты можешь: отвечать на вопросы, помогать с модерацией, "
            "показывать статистику группы, пересказывать обсуждения, "
            "помогать с командами Ирис. "
            "Ты НЕ можешь в группе: отправлять сообщения в другие чаты, "
            "показывать удалённые из ЛС, управлять автоответом, рассылкой, "
            "заметками — это работает только в ЛС с владельцем.\n\n"
            "Групповые команды: /groupstats, /groupsummary, /top, /groupsearch\n"
            "Модерация: /warn, /mute, /kick, /ban, /rules, /moder\n\n"
            f"{_IRIS_KNOWLEDGE}\n"
            f"{_HUMAN_STYLE}"
        )
    else:
        system_prompt = (
            "Ты — gemeni, AI-помощник в личных сообщениях Telegram. "
            "Ты подключён как бизнес-бот через Telegram Business API. "
            "Сейчас ты в ЛИЧНОМ ЧАТЕ (ЛС) с пользователем. "
            "Ты помнишь весь диалог с пользователем.\n\n"
            f"{_BOT_SELF_KNOWLEDGE}\n"
            f"{_IRIS_KNOWLEDGE}\n"
            f"{_HUMAN_STYLE}"
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
        "Ты — gemeni, AI-помощник в Telegram с доступом к истории чатов. "
        "Пользователь спрашивает о том, что происходит в его чатах. "
        "Используй предоставленную историю сообщений чтобы ответить.\n\n"
        f"{_IRIS_KNOWLEDGE}\n"
        f"{_HUMAN_STYLE}"
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
                "Ты — gemeni, AI-помощник в Telegram. Описывай изображения. "
                "Отвечай подробно и полезно на русском.\n" + _HUMAN_STYLE
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
