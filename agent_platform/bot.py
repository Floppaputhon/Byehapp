"""Master entry point.

Run with:

    python -m agent_platform.bot

Required env vars (see ``.env.example``):
* ``MASTER_BOT_TOKEN``
* ``OWNER_ID``
* ``MASTER_PIN``
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

from dotenv import load_dotenv

from .core.ai_router import AIRouter
from .core.crypto import Vault, generate_salt
from .core.db import Database
from .core.intent_router import IntentRouter
from .core.orchestrator import AgentOrchestrator, AgentRuntime
from .handlers.chat import ChatDispatcher
from .handlers.master import MasterCommands
from .handlers.onboarding import OnboardingDispatcher
from .integrations import IntegrationRegistry

logger = logging.getLogger("agent_platform")


def _load_settings() -> tuple[str, int, str, str]:
    here = Path(__file__).parent
    load_dotenv(here / ".env")

    token = os.environ.get("MASTER_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("MASTER_BOT_TOKEN is required")

    try:
        owner_id = int(os.environ.get("OWNER_ID", "0"))
    except ValueError as exc:
        raise SystemExit("OWNER_ID must be an integer") from exc
    if owner_id <= 0:
        raise SystemExit("OWNER_ID is required (Telegram user id)")

    pin = os.environ.get("MASTER_PIN", "").strip()
    if not pin:
        raise SystemExit("MASTER_PIN is required (used to encrypt all secrets)")

    db_path = os.environ.get("DB_PATH") or str(here / "agent_platform.db")
    return token, owner_id, pin, db_path


async def _bootstrap_vault(db: Database, pin: str) -> Vault:
    salt_hex = await db.get_meta("kdf_salt")
    if salt_hex:
        salt = bytes.fromhex(salt_hex)
    else:
        salt = generate_salt()
        await db.set_meta("kdf_salt", salt.hex())
    vault = Vault.from_pin(pin, salt)

    # Verification token — encrypted once on first launch, decrypted on every
    # subsequent launch to detect a wrong PIN immediately.
    canary = await db.get_meta("vault_canary")
    if canary is None:
        await db.set_meta("vault_canary", vault.encrypt("ok"))
    else:
        if vault.try_decrypt(canary) != "ok":
            raise SystemExit(
                "MASTER_PIN does not match the one used to initialise the "
                "database. Either restore the original PIN or delete the DB "
                "and start fresh."
            )
    return vault


async def amain() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    token, owner_id, pin, db_path = _load_settings()

    db = Database(db_path)
    await db.connect()
    vault = await _bootstrap_vault(db, pin)

    ai_router = AIRouter(db, vault)
    intent_router = IntentRouter(ai_router)
    integrations = IntegrationRegistry(db, vault)

    # Forward declaration trick: the orchestrator needs an installer callback
    # which itself needs the orchestrator. We pre-build the dispatchers with a
    # reference to a mutable holder, then create the orchestrator and set the
    # holder. This keeps `__init__` signatures honest.
    box: dict[str, AgentOrchestrator] = {}

    chat_dispatcher = ChatDispatcher(
        orchestrator=None,  # filled below before any handler runs
        db=db,
        ai_router=ai_router,
        intent_router=intent_router,
        integrations=integrations,
        owner_id=owner_id,
    )
    master_commands = MasterCommands(
        orchestrator=None, ai_router=ai_router, owner_id=owner_id
    )
    onboarding = OnboardingDispatcher(orchestrator=None, db=db)

    async def installer(runtime: AgentRuntime) -> None:
        orch = box["orch"]
        runtime.application.bot_data["orchestrator"] = orch
        runtime.application.bot_data["onboarding_dispatcher"] = onboarding
        if runtime.record is not None:
            runtime.application.bot_data["agent_record"] = runtime.record
        master_commands.install(runtime)
        onboarding.install(runtime)
        chat_dispatcher.install(runtime)

    orchestrator = AgentOrchestrator(
        db=db,
        vault=vault,
        master_token=token,
        owner_id=owner_id,
        handler_installer=installer,
    )
    box["orch"] = orchestrator
    chat_dispatcher._orch = orchestrator           # noqa: SLF001
    master_commands._orch = orchestrator           # noqa: SLF001
    onboarding.orchestrator = orchestrator

    await orchestrator.start()
    logger.info("Platform online. Press Ctrl+C to stop.")

    stop_event = asyncio.Event()

    def _request_stop(*_):
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # e.g. on Windows / non-main thread
            signal.signal(sig, lambda *_: _request_stop())

    try:
        await stop_event.wait()
    finally:
        logger.info("Shutting down…")
        await orchestrator.stop()
        await db.close()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
