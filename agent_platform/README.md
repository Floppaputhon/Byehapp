# Agent Platform — TeleClaw-killer

Multi-agent Telegram-платформа: **один мастер-бот** управляет любым количеством **сабагентов** (отдельных Telegram-ботов), каждый со своей ролью, персоной и доступом к одним и тем же тулзам.

## Что внутри

| Компонент | Файлы |
|---|---|
| Мастер-бот + динамическое создание сабагентов | `bot.py`, `core/orchestrator.py` |
| Onboarding-мастер (4 кнопки → динамический follow-up → системный промпт) | `handlers/onboarding.py` |
| LLM-driven intent routing (мультиязычное) | `core/intent_router.py` |
| Мульти-провайдер AI (OpenAI, Anthropic, Gemini, OpenRouter, Groq, DeepSeek, Mistral, xAI, custom) | `providers/` |
| Шифрование всех секретов Fernet'ом (master PIN → PBKDF2) | `core/crypto.py` |
| Интеграции как тулзы LLM (демо: GitHub) | `integrations/` |
| Жёсткая блокировка генерации изображений/видео | `integrations/registry.py` |

## Архитектура одной картинкой

```
                ┌──────────────────────────────┐
                │      MASTER BOT (you)        │
                │  /newagent /addkey /listkeys │
                └──────────────┬───────────────┘
                               │ владелец
                               ▼
                ┌──────────────────────────────┐
                │   AgentOrchestrator (async)  │
                │  spawns Telegram Apps for…   │
                └──┬───────────┬───────────┬───┘
                   ▼           ▼           ▼
              SubAgent#1   SubAgent#2   SubAgent#N
              (Бизнес)     (Модерация)  (Ролевая)
                   │           │           │
                   └───────────┴───────────┘
                               │
                               ▼
              ┌───────────────────────────────┐
              │ IntentRouter ─▶ tools (LLM)   │
              │ AIRouter ─▶ providers (fallback)
              │ IntegrationRegistry ─▶ GitHub │
              │ Encrypted vault (Fernet)      │
              └───────────────────────────────┘
```

## Быстрый старт

```bash
cd agent_platform
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Заполни MASTER_BOT_TOKEN, OWNER_ID, MASTER_PIN, затем:
python -m agent_platform.bot
```

В мастер-боте:

```
/start          # справка
/addkey         # ⌨ диалог: бот покажет кнопки провайдеров → попросит ключ → удалит сообщение с ним
/listkeys       # что уже подключено
/newagent       # ⌨ диалог: бот попросит токен от BotFather следующим сообщением
/listagents
/cancel         # прервать любой диалог
```

И `/addkey openai sk-...`, `/newagent 1234:AAA...` тоже работают (если хочешь делать одним сообщением). Любую команду можно прервать `/cancel`.

Открываешь чат с сабагентом → `/start` → выбираешь одну из 4 ролей через инлайн-кнопку → отвечаешь на follow-up → готово, бот настроен.

## Onboarding мастер-сабагента

Поток (см. `handlers/onboarding.py`):

1. **Step 1 — Purpose Selection.** Инлайн-клавиатура ровно с 4 опциями:
   1. Бизнес, работа
   2. Модерация
   3. Ролевая игра
   4. Другое
2. **Step 2 — Dynamic Follow-up.** В зависимости от выбора задаётся прицельный вопрос (модерация → «правила фильтрации мата/спама», ролевая → «опиши персонажа», и т.п.).
3. **Step 3 — System Prompt Injection.** Ответ сабагента в free-form → компилируется в системный промпт через шаблон persona и сохраняется в БД (`sub_agents.system_prompt`).

Состояние мастера хранится в таблице `onboarding_state` — если бот рестартанёт прямо посреди визарда, прогресс не потеряется.

## Language-agnostic intent routing

**Никакого хардкода команд для интеграций.** Любой текст пользователя проходит через LLM с подключёнными tool-схемами. Модель сама понимает что нужно — независимо от языка, сленга и опечаток. Примеры, которые **все** маршрутизируются в `connect_github`:

- `"connect github"`
- `"законнектись к гитхабу"`
- `"подруби гидхаб пожалуйста"`
- `"mach verbindung mit github"`
- `"привяжи git"`

Технически:
1. `IntentRouter.classify` отправляет сообщение в выбранный AI-провайдер с `tool_choice="auto"` и системным промптом, требующим вызвать тулзу только при явном совпадении интента.
2. Если LLM сделал `tool_call` — выполняем через `IntegrationRegistry`, возвращаем результат пользователю.
3. Если LLM ответил текстом — это обычный chat, пускаем дальше в `ChatDispatcher` с персоной сабагента.

Никаких регэкспов в этом пайплайне нет.

## Поддерживаемые AI-провайдеры

Все добавляются через `/addkey`:

| id | base URL | tool use | vision |
|---|---|---|---|
| `openai` | `https://api.openai.com/v1` | ✓ | ✓ |
| `anthropic` | `https://api.anthropic.com/v1` | ✓ | ✓ |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta` | ✓ | ✓ |
| `openrouter` | `https://openrouter.ai/api/v1` | ✓ | model-dep |
| `groq` | `https://api.groq.com/openai/v1` | ✓ | model-dep |
| `deepseek` | `https://api.deepseek.com/v1` | ✓ | — |
| `mistral` | `https://api.mistral.ai/v1` | ✓ | — |
| `xai` | `https://api.x.ai/v1` | ✓ | model-dep |
| `custom` | задаётся через `base_url` | depends | depends |

`AIRouter` автоматически фолбэчит при rate-limit'е или ошибке провайдера, пробуя остальные ключи в порядке `is_default → остальные`.

## Безопасность

- **Master PIN** → PBKDF2-SHA256 (600 000 итераций) + per-DB salt → Fernet key
- **Vault canary** в `meta` — неверный PIN при рестарте → `SystemExit` сразу
- **Шифруются:** токены сабагентов, API-ключи AI, токены интеграций, vault entries
- **Никогда не пишутся в логи:** токены и API-ключи. Сообщение с `/addkey <key>` бот пытается удалить сразу после сохранения
- **Owner-only:** все команды мастера принимают только `OWNER_ID`. Сабагенты отвечают всем, но используют ключи и интеграции **только** владельца

## Ограничение генерации медиа

Жёстко зафиксировано в `integrations/registry.py`:

```python
BLOCKED_TOOL_PATTERNS = (
    "generate_image", "create_image", "image_generation",
    "edit_image", "modify_image", "animate_image",
    "generate_video", "create_video", "video_generation",
    "render_video", "midjourney", "stable_diffusion",
    "dalle", "sora",
)
```

- Регистрация тулз с такими именами **игнорируется** на старте (даже если кто-то добавит интеграцию).
- Любой `dispatch()` на блокированное имя → `ToolResult(ok=False, error="media_generation_blocked")` с понятным сообщением пользователю.
- Системные промпты во всех персонах (`handlers/onboarding.py`) явно говорят сабагенту: «генерация изображений и видео запрещена».

## Тесты

```bash
agent_platform/.venv/bin/python -m pytest agent_platform/tests -v
```

9 smoke-тестов проверяют:
- vault round-trip
- отказ при неверном PIN
- наличие всех провайдеров
- сериализацию tool spec'а под OpenAI / Anthropic / Gemini форматы
- блокировку media-generation тулз
- регистрацию GitHub-тулз
- шифрование AI-ключей в БД
- персистентность onboarding state
- непустоту BLOCKED_TOOL_PATTERNS

## Что ещё в работе (следующие PR'ы)

- **PR #2 — Tools + OAuth integrations:** long-term memory (vector store), web search + scraper, task scheduler, vision/Whisper, sandbox shell, Google OAuth (Drive/Calendar/Gmail), Notion, Linear
- **PR #3 — Polish:** дополнительные персоны, бурмалда-режим как опция Business-сабагента, импорт полезных утилит из старого `telegram_bot/`, расширенный deployment-гайд (Termux, Cloudflare Worker proxy, systemd)
