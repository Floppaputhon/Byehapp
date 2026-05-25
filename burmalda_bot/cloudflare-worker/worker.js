/**
 * Burmalda Bot — Cloudflare Workers edition.
 *
 * Serverless Telegram Business API auto-responder.
 *
 * Environment variables (set in Worker → Settings → Variables):
 *   BOT_TOKEN        - Telegram Bot API token (Secret)
 *   WEBHOOK_SECRET   - Optional, recommended. If set, must match
 *                      X-Telegram-Bot-Api-Secret-Token header. Set the same
 *                      value via setWebhook?secret_token=...
 *
 * Endpoints:
 *   POST /webhook        - Telegram update intake (configure via setWebhook).
 *   GET  /setup?token=…  - Helper to register the webhook. token must equal BOT_TOKEN.
 *   GET  /                - Health check.
 */

const MAGIC_WORD = "бурмалда";

const WARNING_TEXT =
  "⚠️ Ваше сообщение недоступно для собеседника. " +
  "Для его доставки необходимо ввести слово «Бурмалда»";
const SEEN_TEXT = "Собеседник увидел ваше сообщение ✅";

const ALLOWED_UPDATES = [
  "business_connection",
  "business_message",
  "edited_business_message",
];

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname === "/" || url.pathname === "") {
      return new Response("Burmalda bot is alive", { status: 200 });
    }

    if (url.pathname === "/setup") {
      return handleSetup(url, env);
    }

    if (url.pathname === "/webhook") {
      return handleWebhook(request, env, ctx);
    }

    return new Response("Not found", { status: 404 });
  },
};

async function handleSetup(url, env) {
  const token = url.searchParams.get("token");
  if (!token || token !== env.BOT_TOKEN) {
    return new Response("Forbidden", { status: 403 });
  }
  const workerUrl = `${url.origin}/webhook`;
  const body = {
    url: workerUrl,
    allowed_updates: ALLOWED_UPDATES,
    drop_pending_updates: false,
  };
  if (env.WEBHOOK_SECRET) {
    body.secret_token = env.WEBHOOK_SECRET;
  }
  const resp = await tgApi(env, "setWebhook", body);
  const data = await resp.json();
  return new Response(JSON.stringify(data, null, 2), {
    status: resp.ok ? 200 : 500,
    headers: { "Content-Type": "application/json" },
  });
}

async function handleWebhook(request, env, ctx) {
  if (request.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }

  if (env.WEBHOOK_SECRET) {
    const header = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (header !== env.WEBHOOK_SECRET) {
      return new Response("Forbidden", { status: 403 });
    }
  }

  let update;
  try {
    update = await request.json();
  } catch (err) {
    return new Response("Bad request", { status: 400 });
  }

  // Process asynchronously so Telegram gets a fast 200.
  ctx.waitUntil(processUpdate(update, env));
  return new Response("ok");
}

async function processUpdate(update, env) {
  const message = update.business_message || update.edited_business_message;
  if (!message) {
    return;
  }
  if (!message.business_connection_id) {
    return;
  }
  if (!message.chat || !message.from) {
    return;
  }

  // In Telegram business chats, message.chat.id is the OTHER user's id
  // (from the business owner's perspective). When from.id !== chat.id the
  // sender is the business owner — skip those so we don't reply to
  // ourselves. When from.id === chat.id it's the counterparty.
  if (message.from.id !== message.chat.id) {
    return;
  }

  const text = (message.text || message.caption || "").toLowerCase();
  const reply = text.includes(MAGIC_WORD) ? SEEN_TEXT : WARNING_TEXT;

  try {
    await tgApi(env, "sendMessage", {
      chat_id: message.chat.id,
      text: reply,
      business_connection_id: message.business_connection_id,
    });
  } catch (err) {
    console.error("sendMessage failed", err);
  }
}

async function tgApi(env, method, body) {
  return fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
