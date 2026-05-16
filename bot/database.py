import sqlite3
import time
from contextlib import contextmanager
from typing import Generator, Optional

from bot.config import DB_PATH, CHAT_HISTORY_LIMIT


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                authenticated INTEGER DEFAULT 0,
                is_admin INTEGER DEFAULT 0,
                created_at REAL DEFAULT (strftime('%s', 'now'))
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp REAL DEFAULT (strftime('%s', 'now'))
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS moderation_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                user_id INTEGER,
                message_text TEXT,
                action TEXT NOT NULL,
                reason TEXT,
                timestamp REAL DEFAULT (strftime('%s', 'now'))
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS business_connections (
                connection_id TEXT PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                owner_username TEXT,
                can_reply INTEGER DEFAULT 0,
                is_enabled INTEGER DEFAULT 1,
                created_at REAL DEFAULT (strftime('%s', 'now'))
            )
        """)

        cursor.execute("""CREATE TABLE IF NOT EXISTS blacklist (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                reason TEXT,
                added_at REAL DEFAULT (strftime('%s', 'now'))
            )""")

        cursor.execute("""CREATE TABLE IF NOT EXISTS templates (
                name TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                created_at REAL DEFAULT (strftime('%s', 'now'))
            )""")

        cursor.execute("""CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_user TEXT NOT NULL,
                note_text TEXT NOT NULL,
                author_id INTEGER,
                created_at REAL DEFAULT (strftime('%s', 'now'))
            )""")

        cursor.execute("""CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                remind_text TEXT NOT NULL,
                remind_at REAL NOT NULL,
                sent INTEGER DEFAULT 0,
                created_at REAL DEFAULT (strftime('%s', 'now'))
            )""")

        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("auto_reply", "off"),
        )
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("reply_target", "all"),
        )
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("bot_name", "AI Assistant"),
        )

        conn.commit()


# --- User auth ---

def is_user_authenticated(user_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT authenticated FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return bool(row and row["authenticated"])


def authenticate_user(user_id: int, username: Optional[str], as_admin: bool = True) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO users (user_id, username, authenticated, is_admin)
               VALUES (?, ?, 1, ?)
               ON CONFLICT(user_id) DO UPDATE SET authenticated=1, is_admin=?, username=?""",
            (user_id, username, int(as_admin), int(as_admin), username),
        )
        conn.commit()


def is_admin(user_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT is_admin FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return bool(row and row["is_admin"])


def get_authenticated_user_count() -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE authenticated = 1"
        ).fetchone()
        return row["cnt"] if row else 0


# --- Settings ---

def get_setting(key: str) -> Optional[str]:
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?",
            (key, value, value),
        )
        conn.commit()


def is_auto_reply_on() -> bool:
    return get_setting("auto_reply") == "on"


# --- Chat history ---

def add_message(chat_id: int, user_id: int, role: str, content: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO chat_history (chat_id, user_id, role, content) VALUES (?, ?, ?, ?)",
            (chat_id, user_id, role, content),
        )
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM chat_history WHERE chat_id = ?", (chat_id,)
        ).fetchone()["cnt"]
        if count > CHAT_HISTORY_LIMIT:
            conn.execute(
                """DELETE FROM chat_history WHERE id IN (
                    SELECT id FROM chat_history WHERE chat_id = ?
                    ORDER BY timestamp ASC LIMIT ?
                )""",
                (chat_id, count - CHAT_HISTORY_LIMIT),
            )
        conn.commit()


def get_chat_history(chat_id: int) -> list[dict[str, str]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT role, content FROM chat_history WHERE chat_id = ? ORDER BY timestamp ASC",
            (chat_id,),
        ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]


# --- Moderation log ---

def log_moderation(chat_id: int, user_id: int, message_text: str, action: str, reason: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO moderation_log (chat_id, user_id, message_text, action, reason) VALUES (?, ?, ?, ?, ?)",
            (chat_id, user_id, message_text, action, reason),
        )
        conn.commit()


# --- Business connections ---

def save_business_connection(
    connection_id: str, owner_id: int, owner_username: Optional[str],
    can_reply: bool, is_enabled: bool,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO business_connections (connection_id, owner_id, owner_username, can_reply, is_enabled)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(connection_id) DO UPDATE SET
                 can_reply=?, is_enabled=?, owner_username=?""",
            (connection_id, owner_id, owner_username, int(can_reply), int(is_enabled),
             int(can_reply), int(is_enabled), owner_username),
        )
        conn.commit()


def get_business_connection_owner(connection_id: str) -> Optional[int]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT owner_id FROM business_connections WHERE connection_id = ? AND is_enabled = 1",
            (connection_id,),
        ).fetchone()
        return row["owner_id"] if row else None


def get_active_business_connections() -> list[dict[str, object]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT connection_id, owner_id, owner_username, can_reply FROM business_connections WHERE is_enabled = 1"
        ).fetchall()
        return [
            {"connection_id": r["connection_id"], "owner_id": r["owner_id"],
             "owner_username": r["owner_username"], "can_reply": bool(r["can_reply"])}
            for r in rows
        ]


# --- Blacklist ---

def add_to_blacklist(user_id: int, username: Optional[str], reason: str = "") -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO blacklist (user_id, username, reason) VALUES (?, ?, ?)",
            (user_id, username, reason),
        )
        conn.commit()


def remove_from_blacklist(user_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM blacklist WHERE user_id = ?", (user_id,))
        conn.commit()
        return cursor.rowcount > 0


def is_blacklisted(user_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute("SELECT 1 FROM blacklist WHERE user_id = ?", (user_id,)).fetchone()
        return row is not None


def get_blacklist() -> list[dict[str, object]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT user_id, username, reason FROM blacklist ORDER BY added_at DESC").fetchall()
        return [{"user_id": r["user_id"], "username": r["username"], "reason": r["reason"]} for r in rows]


# --- Templates ---

def save_template(name: str, content: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO templates (name, content) VALUES (?, ?)",
            (name, content),
        )
        conn.commit()


def get_template(name: str) -> Optional[str]:
    with get_connection() as conn:
        row = conn.execute("SELECT content FROM templates WHERE name = ?", (name,)).fetchone()
        return row["content"] if row else None


def delete_template(name: str) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM templates WHERE name = ?", (name,))
        conn.commit()
        return cursor.rowcount > 0


def get_all_templates() -> list[dict[str, str]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT name, content FROM templates ORDER BY name").fetchall()
        return [{"name": r["name"], "content": r["content"]} for r in rows]


# --- Notes ---

def add_note(target_user: str, note_text: str, author_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO notes (target_user, note_text, author_id) VALUES (?, ?, ?)",
            (target_user, note_text, author_id),
        )
        conn.commit()


def get_notes(target_user: str) -> list[dict[str, object]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT note_text, created_at FROM notes WHERE target_user = ? ORDER BY created_at DESC LIMIT 10",
            (target_user,),
        ).fetchall()
        return [{"text": r["note_text"], "created_at": r["created_at"]} for r in rows]


# --- Reminders ---

def add_reminder(chat_id: int, user_id: int, text: str, remind_at: float) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO reminders (chat_id, user_id, remind_text, remind_at) VALUES (?, ?, ?, ?)",
            (chat_id, user_id, text, remind_at),
        )
        conn.commit()
        return cursor.lastrowid or 0


def get_pending_reminders() -> list[dict[str, object]]:
    now = time.time()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, chat_id, user_id, remind_text FROM reminders WHERE remind_at <= ? AND sent = 0",
            (now,),
        ).fetchall()
        return [
            {"id": r["id"], "chat_id": r["chat_id"], "user_id": r["user_id"], "text": r["remind_text"]}
            for r in rows
        ]


def mark_reminder_sent(reminder_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,))
        conn.commit()


def get_moderation_stats_today() -> dict[str, int]:
    today_start = int(time.time()) - (int(time.time()) % 86400)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as total FROM moderation_log WHERE timestamp >= ?",
            (today_start,),
        ).fetchone()
        blocked = conn.execute(
            "SELECT COUNT(*) as cnt FROM moderation_log WHERE timestamp >= ? AND action = 'blocked'",
            (today_start,),
        ).fetchone()
        return {
            "moderated": row["total"] if row else 0,
            "blocked": blocked["cnt"] if blocked else 0,
        }
