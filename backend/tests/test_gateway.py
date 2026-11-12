"""LLM gateway: routing, retries, fallback, structured output, timeouts, usage records."""

import uuid

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.ai.errors import LLMErrorKind as K
from app.ai.errors import LLMGatewayError
from app.ai.gateway import ExecutionRecord, LLMGateway, db_recorder, extract_json
from app.models import ModelExecution
from tests.fakes import FakeProvider, Sleep, err, test_settings


class Answer(BaseModel):
    product: str
    quantity: int


GOOD = '{"product": "laptop", "quantity": 5}'


def gw(*providers, retries=2, recorder=None):
    records: list[ExecutionRecord] = []

    async def collect(rec):
        records.append(rec)

    g = LLMGateway(list(providers), settings=test_settings(llm_max_retries=retries), recorder=recorder or collect)
    return g, records


async def generate(g):
    return await g.generate(Answer, system="sys", prompt="5 laptops", purpose="test")


async def test_primary_success():
    cloud, local = FakeProvider("anthropic", [GOOD]), FakeProvider("ollama", [GOOD])
    g, records = gw(cloud, local)
    res = await generate(g)
    assert res.data == Answer(product="laptop", quantity=5)
    assert (res.provider, res.fallback_used, res.attempts) == ("anthropic", False, 1)
    assert local.calls == []
    assert [(r.provider, r.success, r.input_tokens, r.output_tokens) for r in records] == [("anthropic", True, 11, 7)]
    assert '"quantity"' in cloud.calls[0]["system"], "schema is included in the prompt"
    assert cloud.calls[0]["json_schema"]["title"] == "Answer"


async def test_cloud_unavailable_falls_back_to_ollama_after_retries():
    cloud = FakeProvider("anthropic", [err(K.UNAVAILABLE)])
    local = FakeProvider("ollama", [GOOD])
    g, records = gw(cloud, local, retries=2)
    res = await generate(g)
    assert (res.provider, res.fallback_used, res.attempts) == ("ollama", True, 3)
    assert len(cloud.calls) == 2  # retried up to the limit
    assert [(r.provider, r.attempt, r.success, r.fallback_used, r.error_type) for r in records] == [
        ("anthropic", 1, False, False, "unavailable"),
        ("anthropic", 2, False, False, "unavailable"),
        ("ollama", 1, True, True, None),
    ]


async def test_auth_error_is_not_retried():
    cloud = FakeProvider("anthropic", [err(K.AUTH)])
    local = FakeProvider("ollama", [GOOD])
    g, _ = gw(cloud, local, retries=3)
    res = await generate(g)
    assert len(cloud.calls) == 1 and res.provider == "ollama"


async def test_rate_limit_retry_limit_then_gateway_error():
    only = FakeProvider("anthropic", [err(K.RATE_LIMIT)])
    g, records = gw(only, retries=3)
    with pytest.raises(LLMGatewayError) as e:
        await generate(g)
    assert len(only.calls) == 3
    assert [x.kind for x in e.value.errors] == [K.RATE_LIMIT] * 3
    assert len(records) == 3


async def test_timeout_is_classified_retried_and_falls_back():
    slow = FakeProvider("anthropic", [Sleep(1, GOOD)], timeout_s=0.05)
    local = FakeProvider("ollama", [GOOD])
    g, records = gw(slow, local, retries=2)
    res = await generate(g)
    assert res.provider == "ollama"
    assert [r.error_type for r in records[:2]] == ["timeout", "timeout"]
    assert all(r.latency_ms < 500 for r in records[:2])


async def test_invalid_json_is_repaired_on_same_provider():
    cloud = FakeProvider("anthropic", ["Sure! here you go", GOOD])
    g, records = gw(cloud, FakeProvider("ollama", [GOOD]))
    res = await generate(g)
    assert (res.provider, res.attempts) == ("anthropic", 2)
    repair_turn = cloud.calls[1]["messages"]
    assert [m.role for m in repair_turn] == ["user", "assistant", "user"]
    assert "invalid" in repair_turn[-1].content
    assert [(r.success, r.error_type) for r in records] == [(False, "invalid_output"), (True, None)]


async def test_schema_mismatch_twice_falls_back():
    cloud = FakeProvider("anthropic", ['{"product": "laptop"}', '{"quantity": "many"}'])
    local = FakeProvider("ollama", [GOOD])
    g, _ = gw(cloud, local)
    res = await generate(g)
    assert res.provider == "ollama" and res.fallback_used
    assert len(cloud.calls) == 2  # original + one repair, no more
    assert [e.kind for e in res.errors] == [K.INVALID_OUTPUT, K.INVALID_OUTPUT]


async def test_every_provider_invalid_raises():
    g, _ = gw(FakeProvider("anthropic", ["nope"]), FakeProvider("ollama", ["[1, 2]"]))
    with pytest.raises(LLMGatewayError) as e:
        await generate(g)
    assert {x.provider for x in e.value.errors} == {"anthropic", "ollama"}
    assert all(x.kind == K.INVALID_OUTPUT for x in e.value.errors)


async def test_unconfigured_providers_are_skipped():
    cloud = FakeProvider("anthropic", [GOOD], configured=False)
    local = FakeProvider("ollama", [GOOD])
    g, _ = gw(cloud, local)
    res = await generate(g)
    assert res.provider == "ollama" and res.fallback_used is False  # first *active* provider
    assert cloud.calls == []


async def test_no_providers_configured():
    g, _ = gw(FakeProvider("anthropic", [GOOD], configured=False))
    with pytest.raises(LLMGatewayError, match="no LLM providers configured"):
        await generate(g)


async def test_unknown_adapter_exception_becomes_unavailable():
    class Boom(FakeProvider):
        async def complete(self, *a, **k):
            self.calls.append(1)
            raise RuntimeError("socket exploded")

    g, records = gw(Boom("anthropic", [GOOD]), FakeProvider("ollama", [GOOD]))
    res = await generate(g)
    assert res.provider == "ollama" and records[0].error_type == "unavailable"


async def test_generate_text_falls_back():
    g, _ = gw(FakeProvider("anthropic", [err(K.AUTH)]), FakeProvider("ollama", ["plain words"]))
    res = await g.generate_text(system="s", prompt="p")
    assert (res.text, res.provider, res.fallback_used) == ("plain words", "ollama", True)


@pytest.mark.parametrize("text", [
    GOOD, f"```json\n{GOOD}\n```", f"Here it is:\n{GOOD}\nThanks", f"```\n{GOOD}```",
])
def test_extract_json_tolerates_wrapping(text):
    assert extract_json(text) == {"product": "laptop", "quantity": 5}


async def test_executions_are_persisted(db):
    purpose = f"persist-{uuid.uuid4().hex[:6]}"
    g, _ = gw(FakeProvider("anthropic", [err(K.UNAVAILABLE)]), FakeProvider("ollama", [GOOD]), recorder=db_recorder)
    await g.generate(Answer, system="s", prompt="p", purpose=purpose)
    rows = (await db.scalars(select(ModelExecution).where(ModelExecution.purpose == purpose).order_by(ModelExecution.created_at))).all()
    assert [(r.provider, r.success, r.fallback_used) for r in rows] == [
        ("anthropic", False, False), ("anthropic", False, False), ("ollama", True, True)]
    assert rows[-1].model == "fake-model" and rows[-1].input_tokens == 11
