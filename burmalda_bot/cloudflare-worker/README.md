# Burmalda Bot — Cloudflare Workers (serverless)

Это **бессерверная** версия Burmalda Bot. Работает на бесплатном плане Cloudflare Workers (100 000 запросов/день), всегда онлайн, без своего сервера.

Логика идентична Python-версии в родительской папке: бот отвечает на сообщения собеседникам, когда подключён к Telegram Business как «чат-бот».

## Что понадобится

- Бесплатный аккаунт [Cloudflare](https://dash.cloudflare.com/sign-up)
- Включённый **Business Mode** у бота в [@BotFather](https://t.me/BotFather) (`/mybots` → бот → Bot Settings → Business Mode → Turn on)
- Telegram Premium и подключённый Telegram Business в твоём аккаунте

## Деплой через веб-дашборд (без CLI, проще всего)

1. Зайди на https://dash.cloudflare.com → **Workers & Pages** → **Create application** → **Create Worker** → дай имя, например `burmalda-bot` → Deploy.
2. Открой созданный воркер → **Edit code** → удали шаблонный код, вставь содержимое [`worker.js`](./worker.js) → **Save and deploy**.
3. **Settings → Variables and Secrets** → добавь:
   - `BOT_TOKEN` (Secret) = твой токен бота
   - `WEBHOOK_SECRET` (Secret, опционально, но рекомендуется) = любая случайная строка, например `openssl rand -hex 16`
4. На странице воркера найди его URL вида `https://burmalda-bot.<твой-аккаунт>.workers.dev`.
5. Зарегистрируй webhook одним из способов:

   **Способ A** — открой в браузере (используем встроенный setup-эндпоинт, требует `BOT_TOKEN` в качестве авторизации):
   ```
   https://burmalda-bot.<твой-аккаунт>.workers.dev/setup?token=<BOT_TOKEN>
   ```
   Ответ должен содержать `"ok": true`.

   **Способ B** — `curl`:
   ```bash
   curl -s "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" \
     -H "Content-Type: application/json" \
     -d '{
       "url": "https://burmalda-bot.<твой-аккаунт>.workers.dev/webhook",
       "allowed_updates": ["business_connection","business_message","edited_business_message"],
       "secret_token": "<WEBHOOK_SECRET, если задал>"
     }'
   ```

6. Подключи бота в Telegram: **Настройки → Telegram Business → Чат-боты** → введи `@POiskKaneEXE_bot`, выбери «Когда не в сети».
7. Готово. Проверь — попроси кого-нибудь написать тебе пока ты оффлайн.

## Деплой через wrangler CLI (если предпочитаешь терминал)

```bash
npm install -g wrangler
cd burmalda_bot/cloudflare-worker
wrangler login
wrangler secret put BOT_TOKEN          # вставь токен бота
wrangler secret put WEBHOOK_SECRET     # любая случайная строка (опционально)
wrangler deploy
# Получишь URL вида https://burmalda-bot.<account>.workers.dev
curl "https://burmalda-bot.<account>.workers.dev/setup?token=<BOT_TOKEN>"
```

## Проверить что webhook стоит

```bash
curl "https://api.telegram.org/bot<BOT_TOKEN>/getWebhookInfo"
```

В ответе `url` должен указывать на твой воркер, `pending_update_count` обычно 0, `last_error_message` — пусто.

## Как откатить (если нужно)

```bash
curl "https://api.telegram.org/bot<BOT_TOKEN>/deleteWebhook"
```

После этого можно вернуться к long-polling версии (Python) из родительской папки.

## Безопасность

- `BOT_TOKEN` и `WEBHOOK_SECRET` хранятся как Secrets в Cloudflare и не видны в логах.
- Если задан `WEBHOOK_SECRET`, воркер проверяет заголовок `X-Telegram-Bot-Api-Secret-Token`, который Telegram присылает с каждым апдейтом — посторонние запросы отбрасываются.
- Эндпоинт `/setup` требует `?token=<BOT_TOKEN>` — без знания токена webhook переставить нельзя.

## Лимиты

Cloudflare Workers free план: 100 000 запросов/день, 10 мс CPU на запрос. Для личного бота этого хватает с большим запасом.
