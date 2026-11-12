"""Scriptable fake LLM provider for gateway / agent tests (no network)."""

import asyncio
import json

from app.ai.errors import LLMError, LLMErrorKind
from app.ai.providers import LLMProvider, ProviderResponse
from app.core.config import Settings


class Sleep:
    def __init__(self, seconds: float, then: str = "{}") -> None:
        self.seconds, self.then = seconds, then


class FakeProvider(LLMProvider):
    """Each call consumes the next script step: str → reply, LLMError → raised, Sleep → delay then reply.
    When the script is exhausted the last step repeats."""

    def __init__(self, name: str, script: list, *, model: str = "fake-model", timeout_s: float = 5, configured: bool = True) -> None:
        self.name, self.model, self.timeout_s, self._configured = name, model, timeout_s, configured
        self.script = list(script)
        self.calls: list[dict] = []

    @property
    def configured(self) -> bool:
        return self._configured

    async def complete(self, system, messages, *, max_tokens, json_schema):
        self.calls.append({"system": system, "messages": list(messages), "json_schema": json_schema})
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(step, LLMError):
            raise step
        if isinstance(step, Sleep):
            await asyncio.sleep(step.seconds)
            step = step.then
        if isinstance(step, dict):
            step = json.dumps(step)
        return ProviderResponse(step, 11, 7)


def err(kind: LLMErrorKind, provider: str = "fake") -> LLMError:
    return LLMError(kind, f"simulated {kind}", provider)


def test_settings(**kw) -> Settings:
    return Settings(**{"llm_max_retries": 2, "llm_retry_backoff_s": 0, **kw})


test_settings.__test__ = False  # not a pytest test


class RoutingProvider(FakeProvider):
    """Answers by which agent is calling (matched on its system prompt).

    routes: {"Intent Agent": reply, "Research Agent": reply, "Evaluation Agent": reply}
    A reply is a str/dict, an LLMError, a Sleep, or a callable(prompt) -> any of those.
    """

    def __init__(self, routes: dict, name: str = "ollama", **kw) -> None:
        super().__init__(name, ["{}"], **kw)
        self.routes = routes

    async def complete(self, system, messages, *, max_tokens, json_schema):
        self.calls.append({"system": system, "messages": list(messages), "json_schema": json_schema})
        key = next((k for k in self.routes if k in system), None)
        if key is None:
            raise AssertionError(f"no fake route for system prompt: {system[:80]}")
        step = self.routes[key]
        if callable(step) and not isinstance(step, (LLMError, Sleep)):
            step = step(messages[-1].content)
        if isinstance(step, LLMError):
            raise step
        if isinstance(step, Sleep):
            await asyncio.sleep(step.seconds)
            step = step.then
        if isinstance(step, dict):
            step = json.dumps(step)
        return ProviderResponse(step, 20, 10)

    def calls_for(self, key: str) -> int:
        return sum(1 for c in self.calls if key in c["system"])
