"""Google Gemini provider (v1beta REST)."""

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


class GeminiProvider(ChatProvider):
    name = "gemini"
    default_model = "gemini-2.0-flash"
    default_base_url = "https://generativelanguage.googleapis.com/v1beta"
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

    @staticmethod
    def _serialize_messages(messages: list[ChatMessage]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m.role == "system":
                continue  # passed separately
            if m.role == "tool":
                out.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": m.name or "",
                                    "response": {"result": m.content or ""},
                                }
                            }
                        ],
                    }
                )
                continue
            role = "model" if m.role == "assistant" else m.role
            parts: list[dict] = []
            if m.content:
                parts.append({"text": m.content})
            for tc in m.tool_calls:
                parts.append(
                    {
                        "functionCall": {
                            "name": tc.name,
                            "args": tc.arguments,
                        }
                    }
                )
            out.append({"role": role, "parts": parts or [{"text": ""}]})
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
            "contents": self._serialize_messages(messages),
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            payload["tools"] = [
                {"functionDeclarations": [t.to_gemini() for t in tools]}
            ]
            if tool_choice == "required":
                payload["toolConfig"] = {
                    "functionCallingConfig": {"mode": "ANY"}
                }
            elif tool_choice == "auto":
                payload["toolConfig"] = {
                    "functionCallingConfig": {"mode": "AUTO"}
                }

        url = (
            f"{self.base_url.rstrip('/')}/models/"
            f"{self.resolve_model(model)}:generateContent?key={self.api_key}"
        )
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code >= 400:
            raise ProviderError(
                f"{self.name} {resp.status_code}: {resp.text[:500]}"
            )
        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return ChatResult(text="", raw=data, model=self.resolve_model(model))

        parts = candidates[0].get("content", {}).get("parts", [])
        text_chunks: list[str] = []
        tool_calls: list[ToolCall] = []
        for part in parts:
            if "text" in part:
                text_chunks.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                tool_calls.append(
                    ToolCall(
                        name=fc.get("name", ""),
                        arguments=fc.get("args") or {},
                    )
                )

        return ChatResult(
            text="".join(text_chunks).strip(),
            tool_calls=tool_calls,
            raw=data,
            model=self.resolve_model(model),
            usage=data.get("usageMetadata"),
        )

    async def list_models(self) -> list[str]:
        url = f"{self.base_url.rstrip('/')}/models?key={self.api_key}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url)
        if resp.status_code >= 400:
            raise ProviderError(
                f"{self.name} models {resp.status_code}: {resp.text[:500]}"
            )
        data = resp.json()
        models: list[str] = []
        for m in data.get("models", []):
            name = m.get("name", "")
            if name.startswith("models/"):
                name = name[len("models/"):]
            if "generateContent" in (m.get("supportedGenerationMethods") or []):
                models.append(name)
        return models
