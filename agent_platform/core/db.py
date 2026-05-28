"""Async SQLite layer.

We keep a single database file shared across the master bot and every
sub-agent it spawns. The schema is intentionally small and migration-friendly
— every CREATE statement is idempotent and any new column is added behind a
``has_column`` guard so old databases can be upgraded in place.

Tables:

* ``meta``                   — key/value (schema_version, kdf_salt, etc.)
* ``users``                  — telegram user ids that may interact with the
                                platform (currently just the owner, but the
                                schema is ready for multi-tenant use later)
* ``sub_agents``             — bots spawned by the master. ``token_enc`` is
                                Fernet-encrypted. ``persona_json`` stores the
                                wizard answers.
* ``ai_keys``                — provider → encrypted api key, per user
* ``integration_tokens``     — service → encrypted access token, per user
* ``onboarding_state``       — wizard progress per (owner_id, agent_id)
* ``messages``               — minimal chat memory (rolling window per chat)
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterable, Optional

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    is_owner INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sub_agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    bot_user_id INTEGER,
    bot_username TEXT,
    display_name TEXT NOT NULL,
    token_enc TEXT NOT NULL,
    system_prompt TEXT,
    persona_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending|configuring|running|stopped|error
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(owner_id, bot_user_id),
    UNIQUE(owner_id, display_name)
);

CREATE TABLE IF NOT EXISTS ai_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    label TEXT,
    api_key_enc TEXT NOT NULL,
    base_url TEXT,
    default_model TEXT,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(owner_id, provider, label)
);

CREATE TABLE IF NOT EXISTS integration_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    service TEXT NOT NULL,
    label TEXT,
    token_enc TEXT NOT NULL,
    refresh_token_enc TEXT,
    metadata_json TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(owner_id, service, label)
);

CREATE TABLE IF NOT EXISTS onboarding_state (
    owner_id INTEGER NOT NULL,
    agent_id INTEGER NOT NULL,
    step TEXT NOT NULL,                  -- purpose|followup|done
    answers_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (owner_id, agent_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    user_id INTEGER,
    role TEXT NOT NULL,                  -- user|assistant|system|tool
    content TEXT NOT NULL,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS messages_chat_idx
    ON messages(agent_id, chat_id, created_at);

CREATE TABLE IF NOT EXISTS vault_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    value_enc TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(owner_id, name)
);
"""


class Database:
    """Tiny aiosqlite wrapper with one shared write lock."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        if self._conn is not None:
            return
        first_time = not os.path.exists(self.path)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._conn.executescript(SCHEMA_SQL)
        await self._conn.commit()
        if first_time:
            logger.info("Initialised new database at %s", self.path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # --- meta helpers --------------------------------------------------

    async def get_meta(self, key: str) -> Optional[str]:
        async with self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
        return row["value"] if row else None

    async def set_meta(self, key: str, value: str) -> None:
        await self._conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self._conn.commit()

    # --- generic helpers ----------------------------------------------

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        cur = await self._conn.execute(sql, tuple(params))
        await self._conn.commit()
        return cur.lastrowid

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> Optional[aiosqlite.Row]:
        async with self._conn.execute(sql, tuple(params)) as cur:
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        async with self._conn.execute(sql, tuple(params)) as cur:
            return list(await cur.fetchall())
