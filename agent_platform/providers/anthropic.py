"""Anthropic Claude provider."""

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


class AnthropicProvider(ChatProvider):
    name = "anthropic"
    default_model = "claude-3-5-sonnet-latest"
    default_base_url = "https://api.anthropic.com/v1"
    api_version = "2023-06-01"
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

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": self.api_version,
            "content-type": "application/json",
        }

    @staticmethod
    def _serialize_messages(messages: list[ChatMessage]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m.role == "system":
                # Anthropic takes system as a top-level field; handled elsewhere.
                continue
            if m.role == "tool":
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id or "",
                                "content": m.content or "",
                            }
                        ],
                    }
                )
                continue
            if m.tool_calls:
                parts: list[dict] = []
                if m.content:
                    parts.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    parts.append(
                        {
                            "type": "tool_use",
                            "id": tc.call_id or "",
                            "name": tc.name,
                            "input": tc.arguments,
                        }
                    )
                out.append({"role": m.role, "content": parts})
            else:
                out.append({"role": m.role, "content": m.content or ""})
        return out

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
        payload: dict = {
            "model": self.resolve_model(model),
            "messages": self._serialize_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [t.to_anthropic() for t in tools]
            if tool_choice == "required":
                payload["tool_choice"] = {"type": "any"}
            elif tool_choice == "auto":
                payload["tool_choice"] = {"type": "auto"}

        url = f"{self.base_url.rstrip('/')}/messages"
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
        if resp.status_code >= 400:
            raise ProviderError(
                f"{self.name} {resp.status_code}: {resp.text[:500]}"
            )
        data = resp.json()

        text_chunks: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_chunks.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(
                        name=block.get("name", ""),
                        arguments=block.get("input") or {},
                        call_id=block.get("id"),
                    )
                )

        return ChatResult(
            text="".join(text_chunks).strip(),
            tool_calls=tool_calls,
            raw=data,
            model=data.get("model"),
            usage=data.get("usage"),
        )

    async def list_models(self) -> list[str]:
        # Anthropic doesn't expose a public listing endpoint that's stable;
        # fall back to a hard-coded short list of widely-available models.
        return [
            "claude-3-5-sonnet-latest",
            "claude-3-5-haiku-latest",
            "claude-3-opus-latest",
        ]
