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

            CREATE TABLE IF NOT EXISTS scheduled_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                business_connection_id TEXT,
                text TEXT NOT NULL,
                send_at TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                is_sent INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS autoreply (
                owner_id INTEGER PRIMARY KEY,
                is_enabled INTEGER DEFAULT 0,
                message TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS quick_replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(owner_id, name)
            );

            CREATE TABLE IF NOT EXISTS known_groups (
                chat_id INTEGER PRIMARY KEY,
                title TEXT DEFAULT '',
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS moderation (
                chat_id INTEGER PRIMARY KEY,
                is_enabled INTEGER DEFAULT 0,
                welcome_msg TEXT DEFAULT '',
                rules TEXT DEFAULT '',
                antiflood_max INTEGER DEFAULT 5,
                antiflood_seconds INTEGER DEFAULT 10,
                bad_words TEXT DEFAULT '',
                warn_limit INTEGER DEFAULT 3
            );

            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                reason TEXT,
                warned_by INTEGER,
                expires_at TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id);
            CREATE INDEX IF NOT EXISTS idx_messages_deleted ON messages(is_deleted);
            CREATE INDEX IF NOT EXISTS idx_reminders_time ON reminders(remind_at);
            CREATE INDEX IF NOT EXISTS idx_pending ON pending_replies(is_answered);
            CREATE INDEX IF NOT EXISTS idx_scheduled ON scheduled_messages(send_at);
            CREATE INDEX IF NOT EXISTS idx_notes_owner ON notes(owner_id);
            CREATE INDEX IF NOT EXISTS idx_warnings_chat ON warnings(chat_id, user_id);
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


async def add_scheduled_message(
    owner_id: int,
    chat_id: int,
    business_connection_id: str | None,
    text: str,
    send_at: str,
) -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO scheduled_messages "
            "(owner_id, chat_id, business_connection_id, text, send_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (owner_id, chat_id, business_connection_id, text, send_at),
        )
        await conn.commit()


async def get_due_scheduled_messages() -> list[dict]:
    now = datetime.datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM scheduled_messages WHERE send_at <= ? AND is_sent = 0",
            (now,),
        )
        rows = await cursor.fetchall()
        result = [dict(r) for r in rows]
        for row in result:
            await conn.execute(
                "UPDATE scheduled_messages SET is_sent = 1 WHERE id = ?",
                (row["id"],),
            )
        await conn.commit()
        return result


async def get_business_connection_for_chat(chat_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT bc.* FROM business_connections bc "
            "JOIN messages m ON m.business_connection_id = bc.connection_id "
            "WHERE m.chat_id = ? AND bc.can_reply = 1 AND bc.is_enabled = 1 "
            "LIMIT 1",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_known_chats() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT DISTINCT chat_id, user_id, first_name, username "
            "FROM messages WHERE user_id IS NOT NULL "
            "GROUP BY chat_id "
            "ORDER BY MAX(date) DESC LIMIT 50"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def find_chat_by_username(username: str) -> dict | None:
    clean = username.lstrip("@")
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT chat_id, first_name, username "
            "FROM messages WHERE username = ? COLLATE NOCASE "
            "ORDER BY date DESC LIMIT 1",
            (clean,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_any_business_connection() -> dict | None:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM business_connections "
            "WHERE can_reply = 1 AND is_enabled = 1 "
            "ORDER BY date DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


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


# ── Notes ────────────────────────────────────────────────────────────


async def add_note(owner_id: int, text: str) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute(
            "INSERT INTO notes (owner_id, text) VALUES (?, ?)",
            (owner_id, text),
        )
        await conn.commit()
        return cursor.lastrowid


async def get_notes(owner_id: int, limit: int = 30) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM notes WHERE owner_id = ? ORDER BY created_at DESC LIMIT ?",
            (owner_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def delete_note(owner_id: int, note_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute(
            "DELETE FROM notes WHERE id = ? AND owner_id = ?",
            (note_id, owner_id),
        )
        await conn.commit()
        return cursor.rowcount > 0


async def search_notes(owner_id: int, query: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM notes WHERE owner_id = ? AND text LIKE ? "
            "ORDER BY created_at DESC LIMIT 20",
            (owner_id, f"%{query}%"),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


# ── Auto-reply ───────────────────────────────────────────────────────


async def set_autoreply(owner_id: int, is_enabled: int, message: str) -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO autoreply (owner_id, is_enabled, message) "
            "VALUES (?, ?, ?)",
            (owner_id, is_enabled, message),
        )
        await conn.commit()


async def get_autoreply(owner_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM autoreply WHERE owner_id = ? AND is_enabled = 1",
            (owner_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


# ── Quick replies ────────────────────────────────────────────────────


async def save_quick_reply(owner_id: int, name: str, text: str) -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO quick_replies (owner_id, name, text) "
            "VALUES (?, ?, ?)",
            (owner_id, name, text),
        )
        await conn.commit()


async def get_quick_replies(owner_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM quick_replies WHERE owner_id = ? ORDER BY name",
            (owner_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_quick_reply(owner_id: int, name: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM quick_replies WHERE owner_id = ? AND name = ? COLLATE NOCASE",
            (owner_id, name),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def delete_quick_reply(owner_id: int, name: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute(
            "DELETE FROM quick_replies WHERE owner_id = ? AND name = ? COLLATE NOCASE",
            (owner_id, name),
        )
        await conn.commit()
        return cursor.rowcount > 0


# ── Contact info ─────────────────────────────────────────────────────


async def get_contact_stats(username: str) -> dict | None:
    clean = username.lstrip("@")
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            """SELECT
                username, first_name, user_id, chat_id,
                COUNT(*) as total_messages,
                SUM(CASE WHEN media_type IS NOT NULL THEN 1 ELSE 0 END) as media_count,
                SUM(CASE WHEN is_deleted = 1 THEN 1 ELSE 0 END) as deleted_count,
                MIN(date) as first_seen,
                MAX(date) as last_seen
            FROM messages
            WHERE username = ? COLLATE NOCASE
            GROUP BY username""",
            (clean,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_all_business_chats() -> list[dict]:
    """Get all unique chats with business connections for broadcast."""
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            """SELECT DISTINCT m.chat_id, m.first_name, m.username, m.user_id,
                bc.connection_id
            FROM messages m
            JOIN business_connections bc
                ON m.business_connection_id = bc.connection_id
            WHERE m.user_id IS NOT NULL
            AND bc.can_reply = 1 AND bc.is_enabled = 1
            GROUP BY m.chat_id
            ORDER BY MAX(m.date) DESC"""
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_pending_with_context(limit_per_chat: int = 5) -> list[dict]:
    """Get pending replies grouped by chat with recent message context."""
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT DISTINCT chat_id, first_name, username, user_id "
            "FROM pending_replies WHERE is_answered = 0 "
            "ORDER BY date DESC LIMIT 30"
        )
        chats = [dict(r) for r in await cursor.fetchall()]

        result = []
        for chat in chats:
            cid = chat["chat_id"]
            cursor = await conn.execute(
                "SELECT text, caption, date, first_name "
                "FROM messages WHERE chat_id = ? "
                "ORDER BY date DESC LIMIT ?",
                (cid, limit_per_chat),
            )
            msgs = [dict(r) for r in await cursor.fetchall()]
            msgs.reverse()
            chat["recent_messages"] = msgs
            result.append(chat)
        return result


async def export_chat_messages(
    chat_id: int | None = None, limit: int = 500
) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        if chat_id:
            cursor = await conn.execute(
                "SELECT * FROM messages WHERE chat_id = ? ORDER BY date ASC LIMIT ?",
                (chat_id, limit),
            )
        else:
            cursor = await conn.execute(
                "SELECT * FROM messages ORDER BY date ASC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def upsert_known_group(chat_id: int, title: str) -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO known_groups (chat_id, title, updated_at) "
            "VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, "
            "updated_at=excluded.updated_at",
            (chat_id, title),
        )
        await conn.commit()


async def get_known_group_chats() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT chat_id, title AS first_name FROM known_groups "
            "ORDER BY updated_at DESC LIMIT 20"
        )
        rows = await cursor.fetchall()
        if rows:
            return [dict(r) for r in rows]
        cursor = await conn.execute(
            "SELECT DISTINCT chat_id, first_name "
            "FROM messages WHERE business_connection_id IS NULL "
            "AND chat_id < 0 "
            "GROUP BY chat_id "
            "ORDER BY MAX(date) DESC LIMIT 20"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


# ── Group-specific queries ───────────────────────────────────────────


async def save_group_message(
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
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            """INSERT OR REPLACE INTO messages
            (business_connection_id, chat_id, message_id, user_id, username,
             first_name, text, media_type, file_id, caption, date)
            VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (chat_id, message_id, user_id, username,
             first_name, text, media_type, file_id, caption, date),
        )
        await conn.commit()


async def get_group_stats(chat_id: int, hours: int = 24) -> list[dict]:
    cutoff = (datetime.datetime.now() - datetime.timedelta(hours=hours)).isoformat()
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            """SELECT
                user_id, first_name, username,
                COUNT(*) as msg_count,
                SUM(CASE WHEN media_type IS NOT NULL THEN 1 ELSE 0 END) as media_count
            FROM messages
            WHERE chat_id = ? AND date > ?
            GROUP BY user_id
            ORDER BY msg_count DESC""",
            (chat_id, cutoff),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_group_top_members(chat_id: int, limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            """SELECT
                user_id, first_name, username,
                COUNT(*) as msg_count,
                MIN(date) as first_msg,
                MAX(date) as last_msg
            FROM messages
            WHERE chat_id = ?
            GROUP BY user_id
            ORDER BY msg_count DESC
            LIMIT ?""",
            (chat_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_group_recent_messages(
    chat_id: int, hours: int = 24, limit: int = 200
) -> list[dict]:
    cutoff = (datetime.datetime.now() - datetime.timedelta(hours=hours)).isoformat()
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM messages WHERE chat_id = ? AND date > ? "
            "ORDER BY date DESC LIMIT ?",
            (chat_id, cutoff, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def search_group_messages(
    chat_id: int, query: str, limit: int = 20
) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM messages WHERE chat_id = ? "
            "AND (text LIKE ? OR caption LIKE ?) "
            "ORDER BY date DESC LIMIT ?",
            (chat_id, f"%{query}%", f"%{query}%", limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


# ── Moderation ───────────────────────────────────────────────────────


async def get_moderation(chat_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM moderation WHERE chat_id = ?", (chat_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def set_moderation(chat_id: int, **kwargs) -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        existing = await get_moderation(chat_id)
        if not existing:
            await conn.execute(
                "INSERT INTO moderation (chat_id) VALUES (?)", (chat_id,)
            )
        for key, value in kwargs.items():
            if key in ("is_enabled", "welcome_msg", "rules",
                       "antiflood_max", "antiflood_seconds", "bad_words"):
                await conn.execute(
                    f"UPDATE moderation SET {key} = ? WHERE chat_id = ?",
                    (value, chat_id),
                )
        await conn.commit()


async def add_warning(
    chat_id: int, user_id: int, reason: str | None, warned_by: int | None,
    expires_at: str | None = None,
) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO warnings (chat_id, user_id, reason, warned_by, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, user_id, reason, warned_by, expires_at),
        )
        await conn.commit()
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM warnings WHERE chat_id = ? AND user_id = ? "
            "AND (expires_at IS NULL OR expires_at > datetime('now'))",
            (chat_id, user_id),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def get_warnings(chat_id: int, user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM warnings WHERE chat_id = ? AND user_id = ? "
            "ORDER BY created_at DESC",
            (chat_id, user_id),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def clear_warnings(chat_id: int, user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "DELETE FROM warnings WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        )
        await conn.commit()


async def remove_last_warning(chat_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute(
            "SELECT id FROM warnings WHERE chat_id = ? AND user_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (chat_id, user_id),
        )
        row = await cursor.fetchone()
        if not row:
            return False
        await conn.execute("DELETE FROM warnings WHERE id = ?", (row[0],))
        await conn.commit()
        return True


async def get_recent_warnings_all(chat_id: int, limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM warnings WHERE chat_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (chat_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_active_warning_count(chat_id: int, user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM warnings WHERE chat_id = ? AND user_id = ? "
            "AND (expires_at IS NULL OR expires_at > datetime('now'))",
            (chat_id, user_id),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0
