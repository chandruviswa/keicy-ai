"""LLM gateway: provider routing, retries, fallback, structured output, usage records.

Policy for each provider, in the order of LLM_PROVIDERS (unconfigured ones are skipped):
  * transient errors (timeout, 429, 5xx, connection) → retry the same provider up to
    LLM_MAX_RETRIES attempts with linear backoff, then fall back to the next provider;
  * auth / bad request → fall back immediately (retrying can't help);
  * reply isn't valid JSON or fails the Pydantic schema → one "repair" turn that shows the
    model its validation errors; if that also fails, fall back to the next provider.
Every provider call is written to `model_executions` (in its own transaction, so the record
survives even if the caller rolls back).

The gateway only produces *reasoning*. It never decides that money moved: financial state
comes from PayPal responses and the database (Parts 3–4).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from app.ai.errors import LLMError, LLMErrorKind, LLMGatewayError
from app.ai.providers import LLMProvider, Message, build_providers
from app.core.config import Settings, get_settings

log = logging.getLogger("procura.ai")

T = TypeVar("T", bound=BaseModel)


@dataclass
class ExecutionRecord:
    provider: str
    model: str
    latency_ms: int
    attempt: int
    fallback_used: bool
    success: bool
    purpose: str | None = None
    agent_run_id: uuid.UUID | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error_type: str | None = None
    error: str | None = None


Recorder = Callable[[ExecutionRecord], Awaitable[None]]


async def db_recorder(rec: ExecutionRecord) -> None:
    """Default recorder: one row in model_executions, committed independently."""
    from app.db.session import SessionLocal
    from app.models import ModelExecution

    try:
        async with SessionLocal() as db:
            db.add(ModelExecution(**rec.__dict__))
            await db.commit()
    except Exception:  # noqa: BLE001 — telemetry must never break an agent
        log.exception("Failed to record model execution")


@dataclass
class Generation(Generic[T]):
    data: T
    provider: str
    model: str
    fallback_used: bool
    attempts: int  # provider calls made in total, including retries and repairs
    latency_ms: int
    raw_text: str = ""
    errors: list[LLMError] = field(default_factory=list)  # failures before success


@dataclass
class TextGeneration:
    text: str
    provider: str
    model: str
    fallback_used: bool
    attempts: int
    latency_ms: int


JSON_INSTRUCTIONS = (
    "\n\nRespond with ONE JSON object that matches this JSON Schema. "
    "No prose, no markdown, no code fences.\nSchema:\n{schema}"
)


def extract_json(text: str) -> Any:
    """Parse model output as JSON, tolerating code fences or a sentence around the object."""
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


class LLMGateway:
    def __init__(
        self,
        providers: list[LLMProvider] | None = None,
        *,
        settings: Settings | None = None,
        recorder: Recorder | None = db_recorder,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        s = settings or get_settings()
        self.providers = providers if providers is not None else build_providers(s)
        self.max_retries = max(1, s.llm_max_retries)
        self.backoff_s = s.llm_retry_backoff_s
        self.recorder = recorder
        self._sleep = sleep

    @property
    def active_providers(self) -> list[LLMProvider]:
        return [p for p in self.providers if p.configured]

    async def status(self) -> list[dict[str, Any]]:
        return [
            {"name": p.name, "model": p.model, "configured": p.configured, "reachable": await p.reachable() if p.configured else False}
            for p in self.providers
        ]

    # ---------- public API ----------

    async def generate(
        self,
        schema: type[T],
        *,
        system: str,
        prompt: str,
        max_tokens: int = 2048,
        purpose: str | None = None,
        agent_run_id: uuid.UUID | None = None,
    ) -> Generation[T]:
        """Structured output validated against `schema`. Raises LLMGatewayError if every provider fails."""
        json_schema = schema.model_json_schema()
        full_system = system + JSON_INSTRUCTIONS.format(schema=json.dumps(json_schema))
        started = time.perf_counter()
        attempts = 0
        errors: list[LLMError] = []

        for index, provider in enumerate(self.active_providers):
            fallback = index > 0
            messages = [Message("user", prompt)]
            repaired = False
            attempt = 0
            while attempt < self.max_retries:
                attempt += 1
                attempts += 1
                rec = dict(attempt=attempt, fallback=fallback, purpose=purpose, agent_run_id=agent_run_id)
                try:
                    resp, latency = await self._call(provider, full_system, messages, max_tokens, json_schema, **rec)
                except LLMError as e:
                    errors.append(e)
                    if e.retryable and attempt < self.max_retries:
                        await self._sleep(self.backoff_s * attempt)
                        continue
                    break  # next provider

                try:
                    data = schema.model_validate(extract_json(resp.text))
                except (json.JSONDecodeError, ValidationError, ValueError) as e:
                    err = LLMError(LLMErrorKind.INVALID_OUTPUT, _short(e), provider.name)
                    errors.append(err)
                    await self._record(provider, latency, success=False, resp=resp, error=err, **rec)
                    if repaired:
                        break  # repair already tried — next provider
                    repaired = True
                    attempt -= 1  # a repair turn doesn't use up a retry
                    messages = [
                        *messages,
                        Message("assistant", resp.text[:4000]),
                        Message("user", f"That reply was invalid: {_short(e)}\nReturn only the corrected JSON object."),
                    ]
                    continue

                await self._record(provider, latency, success=True, resp=resp, **rec)
                return Generation(
                    data=data,
                    provider=provider.name,
                    model=provider.model,
                    fallback_used=fallback,
                    attempts=attempts,
                    latency_ms=_ms(started),
                    raw_text=resp.text,
                    errors=errors,
                )

        raise LLMGatewayError(errors)

    async def generate_text(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 1024,
        purpose: str | None = None,
        agent_run_id: uuid.UUID | None = None,
    ) -> TextGeneration:
        """Free text (e.g. explanations). Same retry/fallback rules, no schema."""
        started = time.perf_counter()
        attempts = 0
        errors: list[LLMError] = []
        for index, provider in enumerate(self.active_providers):
            for attempt in range(1, self.max_retries + 1):
                attempts += 1
                rec = dict(attempt=attempt, fallback=index > 0, purpose=purpose, agent_run_id=agent_run_id)
                try:
                    resp, latency = await self._call(provider, system, [Message("user", prompt)], max_tokens, None, **rec)
                    await self._record(provider, latency, success=True, resp=resp, **rec)
                    return TextGeneration(resp.text, provider.name, provider.model, index > 0, attempts, _ms(started))
                except LLMError as e:
                    errors.append(e)
                    if not (e.retryable and attempt < self.max_retries):
                        break
                    await self._sleep(self.backoff_s * attempt)
        raise LLMGatewayError(errors)

    # ---------- internals ----------

    async def _call(self, provider, system, messages, max_tokens, json_schema, **rec) -> tuple[Any, int]:
        """One provider call. Failures are recorded here; the caller records the success/invalid outcome."""
        started = time.perf_counter()
        try:
            # Hard ceiling on top of the SDK timeout, so a hung connection can't stall an agent.
            resp = await asyncio.wait_for(
                provider.complete(system, messages, max_tokens=max_tokens, json_schema=json_schema),
                timeout=provider.timeout_s,
            )
        except TimeoutError as e:
            err = LLMError(LLMErrorKind.TIMEOUT, f"no response within {provider.timeout_s}s", provider.name)
        except LLMError as e:
            err = e
        except Exception as e:  # noqa: BLE001 — unknown adapter failure: treat as unavailable
            err = LLMError(LLMErrorKind.UNAVAILABLE, f"{type(e).__name__}: {e}", provider.name)
        else:
            return resp, _ms(started)
        log.warning("llm call failed provider=%s attempt=%s kind=%s", provider.name, rec["attempt"], err.kind)
        await self._record(provider, _ms(started), success=False, error=err, **rec)
        raise err

    async def _record(self, provider, latency_ms, *, attempt, fallback, purpose, agent_run_id, success, resp=None, error=None):
        if not self.recorder:
            return
        await self.recorder(
            ExecutionRecord(
                provider=provider.name,
                model=provider.model,
                latency_ms=latency_ms,
                attempt=attempt,
                fallback_used=fallback,
                success=success,
                purpose=purpose,
                agent_run_id=agent_run_id,
                input_tokens=getattr(resp, "input_tokens", None),
                output_tokens=getattr(resp, "output_tokens", None),
                error_type=str(error.kind) if error else None,
                error=error.message[:2000] if error else None,
            )
        )


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _short(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(f"{'.'.join(map(str, x['loc'])) or 'root'}: {x['msg']}" for x in e.errors()[:8])
    return str(e)[:300]


_gateway: LLMGateway | None = None


def get_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway
