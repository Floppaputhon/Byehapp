"""OpenAI Chat Completions client, also reused by all OpenAI-compatible providers.

Most third-party providers (OpenRouter, Groq, DeepSeek, Mistral, xAI, ollama,
custom OpenAI-compatible deployments) expose the exact OpenAI Chat Completions
API. We keep a single implementation here and parameterise it by base URL and
default model.
"""

from __future__ import annotations

from typing import Optional

import httpx

from .base import (
    Capabilities,
    ChatMessage,
    ChatProvider,
    ChatResult,
    ProviderError,
    ToolCall,
    ToolSpec,
)


class OpenAICompatibleProvider(ChatProvider):
    name = "openai"
    default_model = "gpt-4o-mini"
    default_base_url = "https://api.openai.com/v1"
    capabilities = Capabilities(
        supports_tools=True,
        supports_vision=True,
        supports_streaming=True,
    )

    def __init__(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        default_model: Optional[str] = None,
    ) -> None:
        super().__init__(
            api_key,
            base_url=base_url or self.default_base_url,
            default_model=default_model,
        )

    # ------------------------------------------------------------------
    # Helpers

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _serialize_message(msg: ChatMessage) -> dict:
        out: dict = {"role": msg.role}
        if msg.content is not None:
            out["content"] = msg.content
        if msg.tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.call_id or f"call_{i}",
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": _dumps(tc.arguments),
                    },
                }
                for i, tc in enumerate(msg.tool_calls)
            ]
        if msg.tool_call_id is not None:
            out["tool_call_id"] = msg.tool_call_id
        if msg.name is not None:
            out["name"] = msg.name
        return out

    @staticmethod
    def _parse_tool_calls(message: dict) -> list[ToolCall]:
        raw = message.get("tool_calls") or []
        calls: list[ToolCall] = []
        for tc in raw:
            fn = tc.get("function") or {}
            calls.append(
                ToolCall.from_arguments_json(
                    name=fn.get("name", ""),
                    arguments_json=fn.get("arguments") or "",
                    call_id=tc.get("id"),
                )
            )
        return calls

    # ------------------------------------------------------------------
    # Public API

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
        payload_messages: list[dict] = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        for m in messages:
            payload_messages.append(self._serialize_message(m))

        payload: dict = {
            "model": self.resolve_model(model),
            "messages": payload_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = [t.to_openai() for t in tools]
            if tool_choice:
                payload["tool_choice"] = tool_choice

        url = f"{self.base_url.rstrip('/')}/chat/completions"
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
        if resp.status_code >= 400:
            raise ProviderError(
                f"{self.name} {resp.status_code}: {resp.text[:500]}"
            )
        data = resp.json()

        try:
            choice = data["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"{self.name}: malformed response") from exc

        text = (choice.get("content") or "").strip()
        tool_calls = self._parse_tool_calls(choice)
        return ChatResult(
            text=text,
            tool_calls=tool_calls,
            raw=data,
            model=data.get("model"),
            usage=data.get("usage"),
        )

    async def list_models(self) -> list[str]:
        url = f"{self.base_url.rstrip('/')}/models"
        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                resp = await client.get(url, headers=self._headers())
            except httpx.HTTPError as exc:
                raise ProviderError(f"{self.name}: {exc}") from exc
        if resp.status_code >= 400:
            raise ProviderError(
                f"{self.name} models {resp.status_code}: {resp.text[:500]}"
            )
        data = resp.json()
        return [m["id"] for m in data.get("data", []) if "id" in m]


class OpenRouterProvider(OpenAICompatibleProvider):
    name = "openrouter"
    default_model = "openrouter/auto"
    default_base_url = "https://openrouter.ai/api/v1"


class GroqProvider(OpenAICompatibleProvider):
    name = "groq"
    default_model = "llama-3.3-70b-versatile"
    default_base_url = "https://api.groq.com/openai/v1"


class DeepSeekProvider(OpenAICompatibleProvider):
    name = "deepseek"
    default_model = "deepseek-chat"
    default_base_url = "https://api.deepseek.com/v1"


class MistralProvider(OpenAICompatibleProvider):
    name = "mistral"
    default_model = "mistral-small-latest"
    default_base_url = "https://api.mistral.ai/v1"


class XAIProvider(OpenAICompatibleProvider):
    name = "xai"
    default_model = "grok-2-latest"
    default_base_url = "https://api.x.ai/v1"


class CustomOpenAICompatibleProvider(OpenAICompatibleProvider):
    """For self-hosted or third-party endpoints with arbitrary base URL."""

    name = "custom"
    default_model = "gpt-4o-mini"
    default_base_url = "http://localhost:1234/v1"


def _dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)
