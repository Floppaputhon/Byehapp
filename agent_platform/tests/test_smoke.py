"""Minimal smoke tests for the platform.

Run with::

    pytest agent_platform/tests
"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent_platform.core.crypto import Vault, generate_salt
from agent_platform.core.db import Database
from agent_platform.core.ai_router import AIRouter
from agent_platform.integrations import IntegrationRegistry
from agent_platform.integrations.registry import BLOCKED_TOOL_PATTERNS
from agent_platform.providers import PROVIDERS, ToolSpec
from agent_platform.providers.base import ChatMessage


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


async def _make_db_and_vault():
    db = Database(":memory:")
    await db.connect()
    salt = generate_salt()
    await db.set_meta("kdf_salt", salt.hex())
    vault = Vault.from_pin("test-pin", salt)
    return db, vault


def test_vault_roundtrip(event_loop):
    async def run():
        db, vault = await _make_db_and_vault()
        token = vault.encrypt("secret data 🔐")
        assert vault.decrypt(token) == "secret data 🔐"
        await db.close()
    event_loop.run_until_complete(run())


def test_vault_wrong_pin_rejected(event_loop):
    async def run():
        db, vault = await _make_db_and_vault()
        token = vault.encrypt("secret")
        salt_hex = await db.get_meta("kdf_salt")
        wrong = Vault.from_pin("other-pin", bytes.fromhex(salt_hex))
        assert wrong.try_decrypt(token) is None
        await db.close()
    event_loop.run_until_complete(run())


def test_providers_registered():
    expected = {
        "openai",
        "anthropic",
        "gemini",
        "openrouter",
        "groq",
        "deepseek",
        "mistral",
        "xai",
        "custom",
    }
    assert expected <= set(PROVIDERS.keys())


def test_toolspec_serialization():
    spec = ToolSpec(
        name="foo",
        description="bar",
        parameters={"type": "object", "properties": {}},
    )
    assert spec.to_openai()["function"]["name"] == "foo"
    assert spec.to_anthropic()["input_schema"]["type"] == "object"
    assert spec.to_gemini()["parameters"]["type"] == "object"


def test_media_generation_blocked(event_loop):
    async def run():
        db, vault = await _make_db_and_vault()
        reg = IntegrationRegistry(db, vault)
        for blocked in ("generate_image", "create_video", "render_video_now"):
            result = await reg.dispatch(blocked, 1, {})
            assert not result.ok
            assert result.error == "media_generation_blocked"
        await db.close()
    event_loop.run_until_complete(run())


def test_github_tools_registered(event_loop):
    async def run():
        db, vault = await _make_db_and_vault()
        reg = IntegrationRegistry(db, vault)
        names = [t.name for t in reg.tools()]
        assert "connect_github" in names
        assert "github_list_issues" in names
        await db.close()
    event_loop.run_until_complete(run())


def test_ai_key_storage_roundtrip(event_loop):
    async def run():
        db, vault = await _make_db_and_vault()
        router = AIRouter(db, vault)
        await router.add_key(
            owner_id=1,
            provider="openai",
            api_key="sk-test-1234",
            make_default=True,
        )
        keys = await router.list_keys(1)
        assert len(keys) == 1
        assert keys[0]["provider"] == "openai"
        assert keys[0]["is_default"]

        # Internal check: stored key is encrypted (not plaintext).
        row = await db.fetchone(
            "SELECT api_key_enc FROM ai_keys WHERE owner_id = 1"
        )
        assert "sk-test-1234" not in row["api_key_enc"]

        # Removal works.
        removed = await router.remove_key(owner_id=1, provider="openai")
        assert removed
        assert await router.list_keys(1) == []
        await db.close()
    event_loop.run_until_complete(run())


def test_onboarding_state_persists(event_loop):
    async def run():
        db, _ = await _make_db_and_vault()
        # Stub a sub-agent record so the FK-style sub-select in
        # onboarding_state inserts cleanly.
        await db.execute(
            "INSERT INTO sub_agents(owner_id, bot_user_id, bot_username, "
            "display_name, token_enc) VALUES(1, 100, 'u', 'a1', 'x')"
        )
        await db.execute(
            "INSERT INTO onboarding_state(owner_id, agent_id, step, answers_json) "
            "VALUES(1, 1, 'followup', ?)",
            (json.dumps({"purpose": "moderation"}),),
        )
        row = await db.fetchone(
            "SELECT step, answers_json FROM onboarding_state WHERE agent_id = 1"
        )
        assert row["step"] == "followup"
        assert json.loads(row["answers_json"])["purpose"] == "moderation"
        await db.close()
    event_loop.run_until_complete(run())


def test_blocked_tool_pattern_constants_non_empty():
    assert len(BLOCKED_TOOL_PATTERNS) >= 10
