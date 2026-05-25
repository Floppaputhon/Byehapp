# Burmalda Bot

Telegram-бот для Telegram Business API. Когда вы оффлайн, бот автоматически отвечает собеседникам:

> ⚠️ Ваше сообщение недоступно для собеседника. Для его доставки необходимо ввести слово «Бурмалда»

Если собеседник пишет «бурмалда» (в любом регистре), бот отвечает:

> Собеседник увидел ваше сообщение ✅

## Как это работает

Бот использует [Telegram Business API](https://core.telegram.org/bots/business). Telegram пересылает боту входящие сообщения только когда вы оффлайн — поведение настраивается в самом Telegram.

## Две реализации

- **Python (long-polling)** — этот каталог. Нужен хост, который держит Python-процесс (VPS, домашний ПК, Termux на телефоне, и т.д.).
- **Cloudflare Workers (webhook, serverless)** — папка [`cloudflare-worker/`](./cloudflare-worker/). **Без своего сервера**, бесплатно, всегда онлайн.

Выбирайте по вкусу. Cloudflare-версия проще в эксплуатации.

## Требования

- Telegram **Premium** (необходим для Telegram Business)
- Python 3.10+

## Установка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Запуск

```bash
export BOT_TOKEN="your-bot-token-here"
python bot.py
```

## Подключение к Telegram Business

1. Откройте Telegram → **Настройки** → **Telegram Business** → **Чат-боты**.
2. Введите username бота (например, `@your_burmalda_bot`).
3. Выберите кому отвечать (всем / только новым / и т.д.).
4. Включите режим «Когда не в сети» — бот будет работать только пока вас нет онлайн.

## Поведение

- Свои собственные сообщения бот игнорирует — отвечает только собеседникам.
- Команды и `business_connection` обновления логируются.
- Бот отвечает на каждое сообщение собеседника (включая отредактированные), пока тот не введёт «бурмалда».

## Деплой

Бот написан в режиме long-polling, поэтому достаточно любого сервера/контейнера, где можно запустить `python bot.py` с переменной окружения `BOT_TOKEN`. Никаких вебхуков и публичных портов не требуется.

Пример unit-файла systemd:

```ini
[Unit]
Description=Burmalda Telegram Bot
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/burmalda-bot
Environment=BOT_TOKEN=your-bot-token-here
ExecStart=/opt/burmalda-bot/.venv/bin/python bot.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```
