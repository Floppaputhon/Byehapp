"""Multi-bot orchestrator.

The :class:`AgentOrchestrator` owns the master bot's
:class:`telegram.ext.Application` and a registry of sub-agent applications
spawned at runtime. Each sub-agent runs in its own asyncio task with its own
Application and its own update polling loop, but shares the database, vault
and AI router with the master.

The orchestrator is responsible for:

* Loading existing sub-agents from the database on start-up.
* Spawning a brand new sub-agent given a freshly-supplied bot token (validating
  the token against ``getMe`` before persisting it).
* Stopping / restarting / removing sub-agents.
* Bridging high-level events (a sub-agent received a message) back to the
  application layer that registered handlers with the orchestrator.

Concurrency model:

* The master Application uses long-polling on its own asyncio task.
* Each sub-agent Application also uses long-polling on its own task.
* All tasks live in the same event loop, so the database (which is single
  writer in SQLite) is naturally serialised.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from telegram import Bot
from telegram.error import InvalidToken, TelegramError
from telegram.ext import Application, ApplicationBuilder

from .crypto import Vault
from .db import Database

logger = logging.getLogger(__name__)


# A "handler installer" is a callable that the application layer registers with
# the orchestrator; it gets called once per Application (master and each
# sub-agent) so the same handlers can be wired up everywhere without leaking
# implementation details into the orchestrator.
HandlerInstaller = Callable[["AgentRuntime"], Awaitable[None]]


@dataclass
class AgentRecord:
    """Persistent representation of a sub-agent."""

    id: int
    owner_id: int
    display_name: str
    bot_user_id: Optional[int]
    bot_username: Optional[str]
    system_prompt: Optional[str]
    persona_json: Optional[str]
    status: str
    last_error: Optional[str]

    @property
    def persona(self) -> dict:
        if not self.persona_json:
            return {}
        try:
            return json.loads(self.persona_json)
        except json.JSONDecodeError:
            return {}


@dataclass
class AgentRuntime:
    """In-memory handle for a running Application (master or sub-agent)."""

    application: Application
    role: str                            # "master" | "sub_agent"
    record: Optional[AgentRecord] = None  # None for the master
    task: Optional[asyncio.Task] = None
    metadata: dict = field(default_factory=dict)

    @property
    def is_master(self) -> bool:
        return self.role == "master"

    @property
    def name(self) -> str:
        if self.is_master:
            return "master"
        if self.record is not None:
            return self.record.display_name
        return "unknown"


class AgentOrchestrator:
    def __init__(
        self,
        db: Database,
        vault: Vault,
        master_token: str,
        owner_id: int,
        handler_installer: HandlerInstaller,
    ) -> None:
        self._db = db
        self._vault = vault
        self._master_token = master_token
        self._owner_id = owner_id
        self._install_handlers = handler_installer
        self._master: Optional[AgentRuntime] = None
        self._sub_agents: dict[int, AgentRuntime] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle

    async def start(self) -> None:
        """Build the master Application and resume any persisted sub-agents."""
        if self._master is not None:
            raise RuntimeError("orchestrator already started")

        master_app = ApplicationBuilder().token(self._master_token).build()
        master_runtime = AgentRuntime(application=master_app, role="master")
        await self._install_handlers(master_runtime)
        self._master = master_runtime

        await master_app.initialize()
        await master_app.start()
        await master_app.updater.start_polling(drop_pending_updates=False)
        logger.info("Master bot started")

        # Resume sub-agents that were running before the last shutdown.
        rows = await self._db.fetchall(
            "SELECT * FROM sub_agents WHERE owner_id = ? AND status IN ('running','configuring','pending')",
            (self._owner_id,),
        )
        for row in rows:
            try:
                await self._spawn_from_row(row)
            except Exception:
                logger.exception("Failed to resume sub-agent id=%s", row["id"])

    async def stop(self) -> None:
        async with self._lock:
            for runtime in list(self._sub_agents.values()):
                await self._stop_runtime(runtime)
            self._sub_agents.clear()
            if self._master is not None:
                await self._stop_runtime(self._master)
                self._master = None

    @staticmethod
    async def _stop_runtime(runtime: AgentRuntime) -> None:
        try:
            if runtime.application.updater is not None:
                await runtime.application.updater.stop()
            await runtime.application.stop()
            await runtime.application.shutdown()
        except Exception:
            logger.exception("Error while stopping %s", runtime.name)

    # ------------------------------------------------------------------
    # Sub-agent management

    async def add_sub_agent(
        self,
        token: str,
        display_name: Optional[str] = None,
    ) -> AgentRecord:
        """Validate a fresh token, persist the sub-agent, and start it."""
        bot = Bot(token)
        try:
            me = await bot.get_me()
        except InvalidToken as exc:
            raise ValueError("Token rejected by Telegram (invalid)") from exc
        except TelegramError as exc:
            raise ValueError(f"Telegram error: {exc}") from exc

        async with self._lock:
            row = await self._db.fetchone(
                "SELECT id FROM sub_agents WHERE owner_id = ? AND bot_user_id = ?",
                (self._owner_id, me.id),
            )
            if row is not None:
                raise ValueError(f"Sub-agent for @{me.username} already exists")

            chosen_name = display_name or me.username or f"agent_{me.id}"
            token_enc = self._vault.encrypt(token)
            agent_id = await self._db.execute(
                "INSERT INTO sub_agents(owner_id, bot_user_id, bot_username, display_name, "
                "token_enc, status) VALUES(?, ?, ?, ?, ?, 'configuring')",
                (self._owner_id, me.id, me.username, chosen_name, token_enc),
            )

        record = await self._fetch_record(agent_id)
        await self._spawn(record, token)
        return record

    async def remove_sub_agent(self, agent_id: int) -> None:
        async with self._lock:
            runtime = self._sub_agents.pop(agent_id, None)
            if runtime is not None:
                await self._stop_runtime(runtime)
            await self._db.execute(
                "DELETE FROM sub_agents WHERE owner_id = ? AND id = ?",
                (self._owner_id, agent_id),
            )

    async def list_sub_agents(self) -> list[AgentRecord]:
        rows = await self._db.fetchall(
            "SELECT * FROM sub_agents WHERE owner_id = ? ORDER BY created_at",
            (self._owner_id,),
        )
        return [self._record_from_row(r) for r in rows]

    async def update_status(
        self,
        agent_id: int,
        status: str,
        last_error: Optional[str] = None,
    ) -> None:
        await self._db.execute(
            "UPDATE sub_agents SET status = ?, last_error = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (status, last_error, agent_id),
        )

    async def update_persona(
        self,
        agent_id: int,
        system_prompt: str,
        persona: dict,
    ) -> None:
        await self._db.execute(
            "UPDATE sub_agents SET system_prompt = ?, persona_json = ?, "
            "status = 'running', updated_at = datetime('now') WHERE id = ?",
            (system_prompt, json.dumps(persona, ensure_ascii=False), agent_id),
        )

    # ------------------------------------------------------------------
    # Lookup

    @property
    def master(self) -> AgentRuntime:
        if self._master is None:
            raise RuntimeError("master is not running")
        return self._master

    def sub_agents(self) -> list[AgentRuntime]:
        return list(self._sub_agents.values())

    async def get_record(self, agent_id: int) -> Optional[AgentRecord]:
        return await self._fetch_record(agent_id)

    async def _fetch_record(self, agent_id: int) -> Optional[AgentRecord]:
        row = await self._db.fetchone(
            "SELECT * FROM sub_agents WHERE id = ?", (agent_id,)
        )
        return self._record_from_row(row) if row else None

    @staticmethod
    def _record_from_row(row) -> AgentRecord:
        return AgentRecord(
            id=row["id"],
            owner_id=row["owner_id"],
            display_name=row["display_name"],
            bot_user_id=row["bot_user_id"],
            bot_username=row["bot_username"],
            system_prompt=row["system_prompt"],
            persona_json=row["persona_json"],
            status=row["status"],
            last_error=row["last_error"],
        )

    # ------------------------------------------------------------------
    # Spawn machinery

    async def _spawn_from_row(self, row) -> None:
        record = self._record_from_row(row)
        token = self._vault.decrypt(row["token_enc"])
        await self._spawn(record, token)

    async def _spawn(self, record: AgentRecord, token: str) -> None:
        try:
            app = ApplicationBuilder().token(token).build()
        except InvalidToken as exc:
            await self.update_status(record.id, "error", f"invalid token: {exc}")
            raise

        runtime = AgentRuntime(application=app, role="sub_agent", record=record)
        await self._install_handlers(runtime)

        try:
            await app.initialize()
            await app.start()
            await app.updater.start_polling(drop_pending_updates=False)
        except Exception as exc:
            logger.exception("Failed to start sub-agent %s", record.display_name)
            await self.update_status(record.id, "error", str(exc))
            try:
                await app.shutdown()
            except Exception:
                pass
            raise

        self._sub_agents[record.id] = runtime
        if record.status != "configuring":
            await self.update_status(record.id, "running")
        logger.info("Sub-agent %s started", record.display_name)
