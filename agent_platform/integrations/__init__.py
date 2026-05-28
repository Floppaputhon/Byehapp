"""Third-party service integrations registered as LLM tools.

Each integration subclasses :class:`base.Integration` and registers tool
schemas via :meth:`tools`. The integration registry exposes a flat list of
:class:`ToolSpec` instances that the intent router sends to the LLM; when the
LLM picks one of them, the corresponding method on the integration is
invoked with the LLM-supplied arguments.
"""

from .base import Integration, ToolHandler, ToolResult  # noqa: F401
from .github import GitHubIntegration  # noqa: F401
from .registry import IntegrationRegistry  # noqa: F401
