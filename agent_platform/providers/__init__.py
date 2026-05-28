"""Pluggable AI providers.

Each provider implements :class:`base.ChatProvider` and registers a name in
the global :data:`PROVIDERS` mapping. The :class:`router.AIRouter` consults a
user's encrypted key store to instantiate live clients on demand.
"""

from .base import (  # noqa: F401
    ChatProvider,
    ProviderError,
    ChatMessage,
    ToolCall,
    ToolSpec,
    Capabilities,
)
from .registry import PROVIDERS, get_provider_cls  # noqa: F401
