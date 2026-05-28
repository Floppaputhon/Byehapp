"""Static registry mapping provider id -> class."""

from __future__ import annotations

from typing import Optional

from .anthropic import AnthropicProvider
from .base import ChatProvider
from .gemini import GeminiProvider
from .openai_compat import (
    CustomOpenAICompatibleProvider,
    DeepSeekProvider,
    GroqProvider,
    MistralProvider,
    OpenAICompatibleProvider,
    OpenRouterProvider,
    XAIProvider,
)


class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"


PROVIDERS: dict[str, type[ChatProvider]] = {
    OpenAIProvider.name: OpenAIProvider,
    AnthropicProvider.name: AnthropicProvider,
    GeminiProvider.name: GeminiProvider,
    OpenRouterProvider.name: OpenRouterProvider,
    GroqProvider.name: GroqProvider,
    DeepSeekProvider.name: DeepSeekProvider,
    MistralProvider.name: MistralProvider,
    XAIProvider.name: XAIProvider,
    CustomOpenAICompatibleProvider.name: CustomOpenAICompatibleProvider,
}


def get_provider_cls(name: str) -> Optional[type[ChatProvider]]:
    return PROVIDERS.get(name.lower())
