"""AI router: figures out which provider/model to use for each request.

The router never holds plaintext credentials in memory longer than necessary:
keys are decrypted at lookup time. Failures cascade through the providers
the user has configured (so a Groq rate-limit can transparently fall back to
OpenAI, etc.).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .crypto import Vault
from .db import Database
from ..providers import (
    ChatMessage,
    ChatProvider,
    ProviderError,
    ToolSpec,
    get_provider_cls,
)
from ..providers.base import ChatResult

logger = logging.getLogger(__name__)


@dataclass
class StoredKey:
    provider: str
    label: Optional[str]
    api_key: str
    base_url: Optional[str]
    default_model: Optional[str]
    is_default: bool


class AIRouter:
    def __init__(self, db: Database, vault: Vault) -> None:
        self._db = db
        self._vault = vault

    # ------------------------------------------------------------------
    # Key management

    async def add_key(
        self,
        *,
        owner_id: int,
        provider: str,
        api_key: str,
        label: Optional[str] = None,
        base_url: Optional[str] = None,
        default_model: Optional[str] = None,
        make_default: bool = False,
    ) -> None:
        cls = get_provider_cls(provider)
        if cls is None:
            raise ValueError(f"unknown provider: {provider}")
        enc = self._vault.encrypt(api_key)
        if make_default:
            await self._db.execute(
                "UPDATE ai_keys SET is_default = 0 WHERE owner_id = ?",
                (owner_id,),
            )
        await self._db.execute(
            "INSERT INTO ai_keys(owner_id, provider, label, api_key_enc, base_url, "
            "default_model, is_default) VALUES(?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(owner_id, provider, label) DO UPDATE SET "
            "api_key_enc = excluded.api_key_enc, base_url = excluded.base_url, "
            "default_model = excluded.default_model, "
            "is_default = excluded.is_default OR ai_keys.is_default",
            (
                owner_id,
                provider,
                label,
                enc,
                base_url,
                default_model,
                1 if make_default else 0,
            ),
        )

    async def remove_key(
        self,
        *,
        owner_id: int,
        provider: str,
        label: Optional[str] = None,
    ) -> bool:
        cur = await self._db.execute(
            "DELETE FROM ai_keys WHERE owner_id = ? AND provider = ? AND "
            "(label IS ? OR label = ?)",
            (owner_id, provider, label, label),
        )
        return cur > 0

    async def list_keys(self, owner_id: int) -> list[dict]:
        rows = await self._db.fetchall(
            "SELECT provider, label, base_url, default_model, is_default, created_at "
            "FROM ai_keys WHERE owner_id = ? ORDER BY provider, label",
            (owner_id,),
        )
        return [dict(r) for r in rows]

    async def set_default(
        self,
        *,
        owner_id: int,
        provider: str,
        model: Optional[str] = None,
    ) -> None:
        if get_provider_cls(provider) is None:
            raise ValueError(f"unknown provider: {provider}")
        await self._db.execute(
            "UPDATE ai_keys SET is_default = 0 WHERE owner_id = ?",
            (owner_id,),
        )
        await self._db.execute(
            "UPDATE ai_keys SET is_default = 1, default_model = COALESCE(?, default_model) "
            "WHERE owner_id = ? AND provider = ?",
            (model, owner_id, provider),
        )

    async def update_model(
        self,
        *,
        owner_id: int,
        provider: str,
        model: str,
    ) -> bool:
        """Update only the default model for a stored provider."""
        cur = await self._db.execute(
            "UPDATE ai_keys SET default_model = ? WHERE owner_id = ? AND provider = ?",
            (model, owner_id, provider),
        )
        return cur > 0

    # ------------------------------------------------------------------
    # Lookups

    async def _stored_keys(
        self,
        owner_id: int,
        prefer: Optional[str] = None,
    ) -> list[StoredKey]:
        rows = await self._db.fetchall(
            "SELECT provider, label, api_key_enc, base_url, default_model, is_default "
            "FROM ai_keys WHERE owner_id = ? ORDER BY is_default DESC, provider",
            (owner_id,),
        )
        keys: list[StoredKey] = []
        for r in rows:
            try:
                api_key = self._vault.decrypt(r["api_key_enc"])
            except Exception:
                logger.exception("Failed to decrypt key for provider=%s", r["provider"])
                continue
            keys.append(
                StoredKey(
                    provider=r["provider"],
                    label=r["label"],
                    api_key=api_key,
                    base_url=r["base_url"],
                    default_model=r["default_model"],
                    is_default=bool(r["is_default"]),
                )
            )
        if prefer:
            keys.sort(key=lambda k: 0 if k.provider == prefer else 1)
        return keys

    def _make_provider(self, key: StoredKey) -> ChatProvider:
        cls = get_provider_cls(key.provider)
        if cls is None:
            raise ProviderError(f"unknown provider: {key.provider}")
        return cls(
            key.api_key,
            base_url=key.base_url,
            default_model=key.default_model,
        )

    # ------------------------------------------------------------------
    # High-level chat

    async def chat(
        self,
        *,
        owner_id: int,
        system: Optional[str],
        messages: list[ChatMessage],
        prefer_provider: Optional[str] = None,
        model: Optional[str] = None,
        tools: Optional[list[ToolSpec]] = None,
        require_tools: bool = False,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> ChatResult:
        candidates = await self._stored_keys(owner_id, prefer=prefer_provider)
        if not candidates:
            raise ProviderError(
                "No AI API keys configured. Add one with the master bot first."
            )

        last_exc: Optional[Exception] = None
        for key in candidates:
            provider = self._make_provider(key)
            if require_tools and not provider.capabilities.supports_tools:
                continue
            try:
                return await provider.chat(
                    system=system,
                    messages=messages,
                    model=model,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tool_choice="auto" if tools else None,
                )
            except ProviderError as exc:
                logger.warning("Provider %s failed: %s", key.provider, exc)
                last_exc = exc
        raise ProviderError(
            f"All configured AI providers failed: {last_exc}"
        )
