"""Real SDK adapters against mocked HTTP: request shape, response parsing, error classification."""

import json

import httpx
import httpx2
import pytest

from app.ai.errors import LLMError, LLMErrorKind as K
from app.ai.providers import AnthropicProvider, Message, OllamaProvider, OpenAIProvider, build_providers
from tests.fakes import test_settings

S = test_settings(anthropic_api_key="test-key", openai_api_key="test-key", anthropic_model="claude-test",
                  openai_model="gpt-test", ollama_model="local-test", ollama_base_url="http://ollama.test")
MSGS = [Message("user", "hello")]


class transport:
    """A mock HTTP transport spec, materialised for whichever HTTP library an SDK uses
    (the Anthropic SDK ships its own `httpx2` fork; OpenAI and Ollama use `httpx`)."""

    def __init__(self, status: int, body: dict | None = None, *, seen: list | None = None, exc: str | None = None) -> None:
        self.status, self.body, self.seen, self.exc = status, body, seen, exc

    def build(self, lib):
        def handler(request):
            if self.seen is not None:
                self.seen.append(request)
            if self.exc:
                raise getattr(lib, self.exc)("simulated", request=request)
            return lib.Response(self.status, json=self.body or {"error": {"type": "error", "message": "boom"}})
        return lib.MockTransport(handler)


ANTHROPIC_OK = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-test",
                "content": [{"type": "text", "text": '{"ok": true}'}], "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 12, "output_tokens": 4}}
OPENAI_OK = {"id": "c1", "object": "chat.completion", "created": 0, "model": "gpt-test",
             "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"ok": true}'}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}}
OLLAMA_OK = {"model": "local-test", "created_at": "2026-01-01T00:00:00Z", "message": {"role": "assistant", "content": '{"ok": true}'},
             "done": True, "prompt_eval_count": 12, "eval_count": 4}


def anthropic(t):
    return AnthropicProvider(S, http_client=httpx2.AsyncClient(transport=t.build(httpx2)))


def openai_(t):
    return OpenAIProvider(S, http_client=httpx.AsyncClient(transport=t.build(httpx)))


def ollama(t):
    return OllamaProvider(S, transport=t.build(httpx))


@pytest.mark.parametrize("make,ok", [(anthropic, ANTHROPIC_OK), (openai_, OPENAI_OK), (ollama, OLLAMA_OK)])
async def test_success_parsing(make, ok):
    seen: list[httpx.Request] = []
    p = make(transport(200, ok, seen=seen))
    resp = await p.complete("be terse", MSGS, max_tokens=50, json_schema={"type": "object"})
    assert (resp.text, resp.input_tokens, resp.output_tokens) == ('{"ok": true}', 12, 4)
    sent = json.loads(seen[0].content)
    assert sent["model"] in {"claude-test", "gpt-test", "local-test"}


async def test_request_shapes():
    seen: list[httpx.Request] = []
    await anthropic(transport(200, ANTHROPIC_OK, seen=seen)).complete("SYS", MSGS, max_tokens=50, json_schema=None)
    body = json.loads(seen[-1].content)
    assert body["system"] == "SYS" and body["messages"] == [{"role": "user", "content": "hello"}] and body["max_tokens"] == 50

    await openai_(transport(200, OPENAI_OK, seen=seen)).complete("SYS", MSGS, max_tokens=50, json_schema={"type": "object"})
    body = json.loads(seen[-1].content)
    assert body["messages"][0] == {"role": "system", "content": "SYS"} and body["response_format"] == {"type": "json_object"}

    await ollama(transport(200, OLLAMA_OK, seen=seen)).complete("SYS", MSGS, max_tokens=50, json_schema={"type": "object"})
    body = json.loads(seen[-1].content)
    assert body["format"] == {"type": "object"} and body["think"] is False and body["stream"] is False


@pytest.mark.parametrize("make", [anthropic, openai_, ollama])
@pytest.mark.parametrize("status,kind", [(401, K.AUTH), (429, K.RATE_LIMIT), (500, K.UNAVAILABLE), (503, K.UNAVAILABLE), (400, K.BAD_REQUEST)])
async def test_http_errors_are_classified(make, status, kind):
    p = make(transport(status, {"error": {"type": "x", "message": "boom"}}))
    with pytest.raises(LLMError) as e:
        await p.complete("s", MSGS, max_tokens=10, json_schema=None)
    assert e.value.kind == kind and e.value.provider == p.name


@pytest.mark.parametrize("make", [anthropic, openai_, ollama])
async def test_connection_refused_is_unavailable(make):
    p = make(transport(0, exc="ConnectError"))
    with pytest.raises(LLMError) as e:
        await p.complete("s", MSGS, max_tokens=10, json_schema=None)
    assert e.value.kind == K.UNAVAILABLE


@pytest.mark.parametrize("make", [anthropic, openai_, ollama])
async def test_read_timeout_is_timeout(make):
    p = make(transport(0, exc="ReadTimeout"))
    with pytest.raises(LLMError) as e:
        await p.complete("s", MSGS, max_tokens=10, json_schema=None)
    assert e.value.kind == K.TIMEOUT


async def test_missing_keys_mean_unconfigured():
    s = test_settings(anthropic_api_key="", openai_api_key="")
    assert not AnthropicProvider(s).configured and not OpenAIProvider(s).configured
    with pytest.raises(LLMError) as e:
        await AnthropicProvider(s).complete("s", MSGS, max_tokens=1, json_schema=None)
    assert e.value.kind == K.NOT_CONFIGURED


def test_build_providers_order_and_validation():
    assert [p.name for p in build_providers(test_settings(llm_providers="openai, anthropic,ollama"))] == ["openai", "anthropic", "ollama"]
    with pytest.raises(ValueError, match="gemini"):
        build_providers(test_settings(llm_providers="anthropic,gemini"))
