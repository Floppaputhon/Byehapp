"""Async SQLite storage for messages, reminders, and business connections."""

import datetime

import aiosqlite

from config import DB_PATH


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_connection_id TEXT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                user_id INTEGER,
                username TEXT,
                first_name TEXT,
                text TEXT,
                media_type TEXT,
                file_id TEXT,
                caption TEXT,
                date TEXT NOT NULL,
                is_deleted INTEGER DEFAULT 0,
                deleted_at TEXT,
                UNIQUE(chat_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                chat_id INTEGER,
                target_name TEXT,
                reminder_text TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                is_done INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS business_connections (
                connection_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                user_chat_id INTEGER,
                date TEXT,
                can_reply INTEGER DEFAULT 0,
                is_enabled INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS pending_replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                user_id INTEGER,
                username TEXT,
                first_name TEXT,
                text TEXT,
                date TEXT NOT NULL,
                is_answered INTEGER DEFAULT 0,
                UNIQUE(chat_id, message_id)
            );

            CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id);
            CREATE INDEX IF NOT EXISTS idx_messages_deleted ON messages(is_deleted);
            CREATE INDEX IF NOT EXISTS idx_reminders_time ON reminders(remind_at);
            CREATE INDEX IF NOT EXISTS idx_pending ON pending_replies(is_answered);
        """)
        await db.commit()


async def save_message(
    business_connection_id: str | None,
    chat_id: int,
    message_id: int,
    user_id: int | None,
    username: str | None,
    first_name: str | None,
    text: str | None,
    media_type: str | None,
    file_id: str | None,
    caption: str | None,
    date: str,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO messages
            (business_connection_id, chat_id, message_id, user_id, username,
             first_name, text, media_type, file_id, caption, date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (business_connection_id, chat_id, message_id, user_id, username,
             first_name, text, media_type, file_id, caption, date),
        )
        await db.commit()


async def mark_deleted(chat_id: int, message_ids: list[int]) -> None:
    now = datetime.datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        for msg_id in message_ids:
            await db.execute(
                "UPDATE messages SET is_deleted = 1, deleted_at = ? "
                "WHERE chat_id = ? AND message_id = ?",
                (now, chat_id, msg_id),
            )
        await db.commit()


async def get_messages_by_ids(chat_id: int, message_ids: list[int]) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        placeholders = ",".join("?" for _ in message_ids)
        cursor = await db.execute(
            f"SELECT * FROM messages WHERE chat_id = ? AND message_id IN ({placeholders})",
            [chat_id, *message_ids],
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_deleted_messages(limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM messages WHERE is_deleted = 1 "
            "ORDER BY deleted_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_recent_messages(
    chat_id: int | None = None, hours: int = 24, limit: int = 200
) -> list[dict]:
    cutoff = (datetime.datetime.now() - datetime.timedelta(hours=hours)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if chat_id:
            cursor = await db.execute(
                "SELECT * FROM messages WHERE chat_id = ? AND date > ? "
                "ORDER BY date DESC LIMIT ?",
                (chat_id, cutoff, limit),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM messages WHERE date > ? "
                "ORDER BY date DESC LIMIT ?",
                (cutoff, limit),
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def add_reminder(
    owner_id: int,
    chat_id: int | None,
    target_name: str,
    text: str,
    remind_at: str,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO reminders (owner_id, chat_id, target_name, reminder_text, remind_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (owner_id, chat_id, target_name, text, remind_at),
        )
        await db.commit()


async def get_due_reminders() -> list[dict]:
    now = datetime.datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM reminders WHERE remind_at <= ? AND is_done = 0",
            (now,),
        )
        rows = await cursor.fetchall()
        result = [dict(r) for r in rows]
        for row in result:
            await db.execute(
                "UPDATE reminders SET is_done = 1 WHERE id = ?", (row["id"],)
            )
        await db.commit()
        return result


async def get_pending_reminders(owner_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM reminders WHERE owner_id = ? AND is_done = 0 "
            "ORDER BY remind_at ASC",
            (owner_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def save_pending_reply(
    chat_id: int,
    message_id: int,
    user_id: int | None,
    username: str | None,
    first_name: str | None,
    text: str | None,
    date: str,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO pending_replies "
            "(chat_id, message_id, user_id, username, first_name, text, date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chat_id, message_id, user_id, username, first_name, text, date),
        )
        await db.commit()


async def mark_replied_in_chat(chat_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE pending_replies SET is_answered = 1 "
            "WHERE chat_id = ? AND is_answered = 0",
            (chat_id,),
        )
        await db.commit()


async def get_pending_replies(limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM pending_replies WHERE is_answered = 0 "
            "ORDER BY date DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def search_messages(query: str, limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM messages WHERE (text LIKE ? OR caption LIKE ?) "
            "ORDER BY date DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_message_stats(hours: int = 24) -> list[dict]:
    cutoff = (datetime.datetime.now() - datetime.timedelta(hours=hours)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT
                chat_id,
                first_name,
                username,
                COUNT(*) as msg_count,
                SUM(CASE WHEN media_type IS NOT NULL THEN 1 ELSE 0 END) as media_count,
                SUM(CASE WHEN is_deleted = 1 THEN 1 ELSE 0 END) as deleted_count
            FROM messages
            WHERE date > ?
            GROUP BY chat_id, user_id
            ORDER BY msg_count DESC""",
            (cutoff,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def save_business_connection(
    connection_id: str,
    user_id: int,
    user_chat_id: int | None,
    date: str,
    can_reply: int,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO business_connections "
            "(connection_id, user_id, user_chat_id, date, can_reply, is_enabled) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (connection_id, user_id, user_chat_id, date, can_reply),
        )
        await db.commit()
