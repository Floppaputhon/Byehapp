"""Abstract base for service integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from ..core.crypto import Vault
from ..core.db import Database
from ..providers import ToolSpec


@dataclass
class ToolResult:
    """Result of executing an LLM-invoked tool call."""

    ok: bool
    summary: str
    data: Optional[dict] = None
    error: Optional[str] = None


# A ToolHandler receives (owner_id, arguments) and returns a ToolResult.
ToolHandler = Callable[[int, dict], Awaitable[ToolResult]]


class Integration:
    """Base class for a service integration.

    Subclasses set :attr:`service` (machine name, e.g. ``"github"``) and
    implement:

    * :meth:`tools()` — list of :class:`ToolSpec` declarations exposed to LLM
    * :meth:`handle(name, owner_id, arguments)` — dispatch to the right method
    * Authentication state stored in :class:`integration_tokens` table.

    The convention is that the very first tool of every integration is
    ``connect_<service>`` so that the LLM intent router can always pick up
    user requests like *"connect github"*, *"подруби гитхаб"*, etc.
    """

    service: str = ""
    display_name: str = ""

    def __init__(self, db: Database, vault: Vault) -> None:
        self._db = db
        self._vault = vault

    # ------------------------------------------------------------------
    # Token storage helpers

    async def _store_token(
        self,
        owner_id: int,
        token: str,
        *,
        label: Optional[str] = None,
        refresh_token: Optional[str] = None,
        metadata: Optional[dict] = None,
        expires_at: Optional[str] = None,
    ) -> None:
        import json as _json

        enc = self._vault.encrypt(token)
        refresh_enc = self._vault.encrypt(refresh_token) if refresh_token else None
        meta_json = _json.dumps(metadata, ensure_ascii=False) if metadata else None
        await self._db.execute(
            "INSERT INTO integration_tokens(owner_id, service, label, token_enc, "
            "refresh_token_enc, metadata_json, expires_at) VALUES(?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(owner_id, service, label) DO UPDATE SET "
            "token_enc = excluded.token_enc, "
            "refresh_token_enc = excluded.refresh_token_enc, "
            "metadata_json = excluded.metadata_json, "
            "expires_at = excluded.expires_at",
            (owner_id, self.service, label, enc, refresh_enc, meta_json, expires_at),
        )

    async def _load_token(self, owner_id: int, label: Optional[str] = None) -> Optional[str]:
        row = await self._db.fetchone(
            "SELECT token_enc FROM integration_tokens "
            "WHERE owner_id = ? AND service = ? AND (label IS ? OR label = ?)",
            (owner_id, self.service, label, label),
        )
        if not row:
            return None
        try:
            return self._vault.decrypt(row["token_enc"])
        except Exception:
            return None

    async def _delete_token(self, owner_id: int, label: Optional[str] = None) -> bool:
        cur = await self._db.execute(
            "DELETE FROM integration_tokens WHERE owner_id = ? AND service = ? AND "
            "(label IS ? OR label = ?)",
            (owner_id, self.service, label, label),
        )
        return cur > 0

    async def is_connected(self, owner_id: int) -> bool:
        return (await self._load_token(owner_id)) is not None

    # ------------------------------------------------------------------
    # Public surface implemented by subclasses

    def tools(self) -> list[ToolSpec]:
        raise NotImplementedError

    async def handle(
        self,
        name: str,
        owner_id: int,
        arguments: dict,
    ) -> ToolResult:
        raise NotImplementedError
