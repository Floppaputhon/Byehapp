import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
VECTORENGINE_API_KEY: str = os.getenv("VECTORENGINE_API_KEY", "")
VECTORENGINE_BASE_URL: str = "https://api.vectorengine.ai/v1"

LLM_MODEL: str = "gemini-3-flash-preview"
LLM_MODEL_FALLBACK: str = "claude-sonnet-4-5-20250929"

AUTH_PASSWORD: str = "Девин топ😎₽"

DB_PATH: str = "bot_data.db"
CHAT_HISTORY_LIMIT: int = 20
