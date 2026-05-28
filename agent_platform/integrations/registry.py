"""Registry that aggregates integrations + media-generation lockdown."""

from __future__ import annotations

import logging

from ..core.crypto import Vault
from ..core.db import Database
from ..providers import ToolSpec
from .base import Integration, ToolResult
from .github import GitHubIntegration

logger = logging.getLogger(__name__)


# Names that the LLM might try to invoke despite our system prompt asking it
# not to. Anything matching this list is refused with an explicit error per
# the platform's policy (no image / video generation).
BLOCKED_TOOL_PATTERNS = (
    "generate_image",
    "create_image",
    "image_generation",
    "edit_image",
    "modify_image",
    "animate_image",
    "generate_video",
    "create_video",
    "video_generation",
    "render_video",
    "midjourney",
    "stable_diffusion",
    "dalle",
    "sora",
)


class MediaGenerationBlockedError(RuntimeError):
    """Raised when something tries to invoke a banned media-generation tool."""


class IntegrationRegistry:
    def __init__(self, db: Database, vault: Vault) -> None:
        self._db = db
        self._vault = vault
        self._by_service: dict[str, Integration] = {}
        self._by_tool: dict[str, Integration] = {}
        self._tool_specs: list[ToolSpec] = []
        self._register_defaults()

    def _register_defaults(self) -> None:
        for cls in (GitHubIntegration,):
            inst = cls(self._db, self._vault)
            self.register(inst)

    def register(self, integration: Integration) -> None:
        self._by_service[integration.service] = integration
        for spec in integration.tools():
            if self._is_blocked(spec.name):
                logger.warning(
                    "Refusing to register media-generation tool %s",
                    spec.name,
                )
                continue
            self._by_tool[spec.name] = integration
            self._tool_specs.append(spec)

    def tools(self) -> list[ToolSpec]:
        return list(self._tool_specs)

    def get_service(self, service: str) -> Integration | None:
        return self._by_service.get(service)

    async def dispatch(
        self,
        tool_name: str,
        owner_id: int,
        arguments: dict,
    ) -> ToolResult:
        if self._is_blocked(tool_name):
            return ToolResult(
                ok=False,
                summary=(
                    "⛔ Генерация и редактирование изображений / видео в этой "
                    "платформе отключены. Попробуй задать вопрос текстом или "
                    "приложить файл — описать смогу."
                ),
                error="media_generation_blocked",
            )
        integration = self._by_tool.get(tool_name)
        if integration is None:
            return ToolResult(
                ok=False,
                summary=f"Tool '{tool_name}' is not registered.",
                error="unknown_tool",
            )
        try:
            return await integration.handle(tool_name, owner_id, arguments)
        except Exception as exc:
            logger.exception("Integration %s failed on %s", integration.service, tool_name)
            return ToolResult(
                ok=False,
                summary=f"Internal error while running {tool_name}: {exc}",
                error="internal",
            )

    @staticmethod
    def _is_blocked(name: str) -> bool:
        n = (name or "").lower()
        return any(pat in n for pat in BLOCKED_TOOL_PATTERNS)
