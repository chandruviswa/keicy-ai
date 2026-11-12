"""Provider adapters. Each turns one chat call into ProviderResponse or a classified LLMError.

Adapters never retry (SDK retries are disabled) — retry/fallback policy lives in the gateway.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx

from app.ai.errors import LLMError, LLMErrorKind, kind_from_status
from app.core.config import Settings


@dataclass
class Message:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class ProviderResponse:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMProvider(ABC):
    name: str
    model: str
    timeout_s: float

    @property
    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    async def complete(
        self, system: str, messages: list[Message], *, max_tokens: int, json_schema: dict[str, Any] | None
    ) -> ProviderResponse: ...

    async def reachable(self) -> bool:
        return self.configured


def _classify_sdk_error(e: Exception, provider: str, sdk) -> LLMError:
    """Shared classification for the Anthropic and OpenAI SDKs (same exception layout)."""
    if isinstance(e, sdk.APITimeoutError):
        return LLMError(LLMErrorKind.TIMEOUT, str(e), provider)
    if isinstance(e, sdk.APIConnectionError):
        return LLMError(LLMErrorKind.UNAVAILABLE, str(e), provider)
    if isinstance(e, sdk.APIStatusError):
        return LLMError(kind_from_status(e.status_code), str(e), provider)
    return LLMError(LLMErrorKind.UNAVAILABLE, f"{type(e).__name__}: {e}", provider)


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, s: Settings, http_client: httpx.AsyncClient | None = None) -> None:
        self.model = s.anthropic_model
        self.timeout_s = s.llm_timeout_s
        self._key = s.anthropic_api_key
        self._client = None
        if self._key:
            import anthropic

            self._sdk = anthropic
            self._client = anthropic.AsyncAnthropic(
                api_key=self._key, max_retries=0, timeout=self.timeout_s, http_client=http_client
            )

    @property
    def configured(self) -> bool:
        return self._client is not None

    async def complete(self, system, messages, *, max_tokens, json_schema):
        if not self._client:
            raise LLMError(LLMErrorKind.NOT_CONFIGURED, "ANTHROPIC_API_KEY not set", self.name)
        try:
            resp = await self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": m.role, "content": m.content} for m in messages],
            )
        except Exception as e:  # noqa: BLE001
            raise _classify_sdk_error(e, self.name, self._sdk) from e
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")
        usage = getattr(resp, "usage", None)
        return ProviderResponse(text, getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None))


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, s: Settings, http_client: httpx.AsyncClient | None = None) -> None:
        self.model = s.openai_model
        self.timeout_s = s.llm_timeout_s
        self._client = None
        if s.openai_api_key:
            import openai

            self._sdk = openai
            self._client = openai.AsyncOpenAI(
                api_key=s.openai_api_key, max_retries=0, timeout=self.timeout_s, http_client=http_client
            )

    @property
    def configured(self) -> bool:
        return self._client is not None

    async def complete(self, system, messages, *, max_tokens, json_schema):
        if not self._client:
            raise LLMError(LLMErrorKind.NOT_CONFIGURED, "OPENAI_API_KEY not set", self.name)
        kwargs: dict[str, Any] = {}
        if json_schema is not None:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = await self._client.chat.completions.create(
                model=self.model,
                max_completion_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, *({"role": m.role, "content": m.content} for m in messages)],
                **kwargs,
            )
        except Exception as e:  # noqa: BLE001
            raise _classify_sdk_error(e, self.name, self._sdk) from e
        usage = resp.usage
        return ProviderResponse(
            resp.choices[0].message.content or "",
            getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None),
        )


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, s: Settings, **client_kwargs: Any) -> None:
        import ollama

        self._sdk = ollama
        self.model = s.ollama_model
        self.timeout_s = s.ollama_timeout_s
        self.base_url = s.ollama_base_url
        self.think = s.ollama_think
        self._client = ollama.AsyncClient(host=self.base_url, timeout=self.timeout_s, **client_kwargs)
        self._client_kwargs = client_kwargs

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    async def reachable(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2, **self._client_kwargs) as c:
                r = await c.get(f"{self.base_url}/api/tags")
                return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def complete(self, system, messages, *, max_tokens, json_schema):
        try:
            resp = await self._client.chat(
                model=self.model,
                messages=[{"role": "system", "content": system}, *({"role": m.role, "content": m.content} for m in messages)],
                # Ollama constrains generation to the JSON schema itself — much more reliable for local models.
                format=json_schema if json_schema is not None else None,
                think=self.think,
                options={"num_predict": max_tokens},
            )
        except self._sdk.ResponseError as e:
            raise LLMError(kind_from_status(getattr(e, "status_code", None)), str(e), self.name) from e
        except httpx.TimeoutException as e:
            raise LLMError(LLMErrorKind.TIMEOUT, str(e) or "timed out", self.name) from e
        except (httpx.HTTPError, ConnectionError, OSError) as e:
            raise LLMError(LLMErrorKind.UNAVAILABLE, str(e) or type(e).__name__, self.name) from e
        return ProviderResponse(
            resp.message.content or "", getattr(resp, "prompt_eval_count", None), getattr(resp, "eval_count", None)
        )


PROVIDER_CLASSES: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
}


def build_providers(s: Settings) -> list[LLMProvider]:
    unknown = [n for n in s.llm_provider_list if n not in PROVIDER_CLASSES]
    if unknown:
        raise ValueError(f"Unknown LLM provider(s) in LLM_PROVIDERS: {unknown}")
    return [PROVIDER_CLASSES[n](s) for n in s.llm_provider_list]
