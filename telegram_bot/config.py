"""Bot configuration — loads settings from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
AI_API_KEY: str = os.getenv("AI_API_KEY", "")
AI_API_URL: str = os.getenv("AI_API_URL", "https://api.vectorengine.ai/v1")
AI_MODEL: str = os.getenv("AI_MODEL", "gpt-4o-mini")
OWNER_ID: int = int(os.getenv("OWNER_ID", "0"))
DB_PATH: str = str(BASE_DIR / "bot_data.db")
