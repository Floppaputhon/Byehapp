"""Common interface for chat-completion AI providers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional


class ProviderError(RuntimeError):
    """Raised when a provider request fails."""


@dataclass
class ToolSpec:
    """JSON-Schema description of a tool the LLM may call."""

    name: str
    description: str
    parameters: dict  # JSON Schema object

    def to_openai(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }

    def to_gemini(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


@dataclass
class ToolCall:
    """A function-call request emitted by the LLM."""

    name: str
    arguments: dict
    call_id: Optional[str] = None  # opaque id used by OpenAI / Anthropic

    @classmethod
    def from_arguments_json(
        cls, name: str, arguments_json: str, call_id: Optional[str] = None
    ) -> "ToolCall":
        try:
            args = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError:
            args = {"_raw": arguments_json}
        return cls(name=name, arguments=args, call_id=call_id)


@dataclass
class ChatMessage:
    """Provider-agnostic chat message."""

    role: str                                  # system|user|assistant|tool
    content: Optional[str] = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: Optional[str] = None         # for role=="tool" responses
    name: Optional[str] = None                 # tool/function name


@dataclass
class ChatResult:
    """Outcome of a single chat completion call."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Optional[Any] = None
    model: Optional[str] = None
    usage: Optional[dict] = None

    @property
    def wants_tool(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class Capabilities:
    """What a provider can do — used by the AI router for selection."""

    supports_tools: bool = False
    supports_vision: bool = False
    supports_streaming: bool = False
    supports_system_prompt: bool = True
    notes: str = ""


class ChatProvider:
    """Abstract base class.

    Concrete providers implement :meth:`chat`. The router and intent
    classifier never instantiate providers directly — they go through
    :class:`agent_platform.providers.registry`.
    """

    name: str = ""
    default_model: str = ""
    capabilities: Capabilities = Capabilities()

    def __init__(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        default_model: Optional[str] = None,
    ) -> None:
        if not api_key:
            raise ProviderError(f"{self.name}: api key is required")
        self.api_key = api_key
        self.base_url = base_url
        if default_model:
            self.default_model = default_model

    async def chat(
        self,
        *,
        system: Optional[str],
        messages: list[ChatMessage],
        model: Optional[str] = None,
        tools: Optional[list[ToolSpec]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        tool_choice: Optional[str] = None,
    ) -> ChatResult:
        raise NotImplementedError

    async def list_models(self) -> list[str]:
        return [self.default_model]

    def resolve_model(self, model: Optional[str]) -> str:
        return model or self.default_model
