import hashlib
import hmac
import json
import logging
import time
from urllib.parse import parse_qs, unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bot.config import TELEGRAM_BOT_TOKEN
from bot.database import (
    get_active_business_connections,
    get_authenticated_user_count,
    get_moderation_stats_today,
    get_setting,
    is_auto_reply_on,
    set_setting,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="Bot Mini App API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BOT_START_TIME = time.time()


def validate_webapp_data(init_data: str) -> dict | None:
    parsed = parse_qs(init_data)
    check_hash = parsed.get("hash", [None])[0]
    if not check_hash:
        return None

    pairs = []
    for part in init_data.split("&"):
        key, _, val = part.partition("=")
        if key != "hash":
            pairs.append(f"{key}={unquote(val)}")
    pairs.sort()
    data_check_string = "\n".join(pairs)

    secret_key = hmac.new(b"WebAppData", TELEGRAM_BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed, check_hash):
        return None

    user_str = parsed.get("user", [None])[0]
    if user_str:
        return json.loads(unquote(user_str))
    return {}


class AutoReplyRequest(BaseModel):
    enabled: bool
    init_data: str = ""


class SettingRequest(BaseModel):
    key: str
    value: str
    init_data: str = ""


@app.get("/api/status")
async def get_status(request: Request):
    uptime_seconds = int(time.time() - BOT_START_TIME)
    hours = uptime_seconds // 3600
    minutes = (uptime_seconds % 3600) // 60
    seconds = uptime_seconds % 60

    mod_stats = get_moderation_stats_today()
    connections = get_active_business_connections()

    reply_target = get_setting("reply_target") or "all"

    return {
        "auto_reply": is_auto_reply_on(),
        "reply_target": reply_target,
        "authenticated_users": get_authenticated_user_count(),
        "moderation": {
            "moderated": mod_stats["moderated"],
            "blocked": mod_stats["blocked"],
        },
        "uptime": {
            "hours": hours,
            "minutes": minutes,
            "seconds": seconds,
            "total_seconds": uptime_seconds,
        },
        "business_connections": [
            {
                "owner_username": c["owner_username"],
                "can_reply": c["can_reply"],
            }
            for c in connections
        ],
    }


@app.post("/api/auto-reply")
async def toggle_auto_reply(req: AutoReplyRequest):
    set_setting("auto_reply", "on" if req.enabled else "off")
    return {"auto_reply": req.enabled}


@app.get("/api/settings/{key}")
async def get_setting_value(key: str):
    value = get_setting(key)
    if value is None:
        raise HTTPException(status_code=404, detail="Setting not found")
    return {"key": key, "value": value}


@app.post("/api/settings")
async def update_setting(req: SettingRequest):
    set_setting(req.key, req.value)
    return {"key": req.key, "value": req.value}


app.mount("/", StaticFiles(directory="webapp/static", html=True), name="static")
