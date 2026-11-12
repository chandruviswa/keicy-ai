"""Tool registry and execution manager.

Every tool call goes through ToolRegistry.execute(), which:
  1. blocks tools outside the calling agent's allow-list, and calls beyond its tool-call limit;
  2. validates arguments against the tool's Pydantic model;
  3. for write tools: requires an idempotency key and returns the stored result instead of
     repeating the operation if that key already succeeded;
  4. runs the handler with a timeout, then validates its result;
  5. commits write tools' changes immediately (so an idempotency record never points at
     rolled-back data);
  6. logs the call to agent_tool_logs + a workflow event, in a separate transaction so the
     trace survives even if the agent later fails.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AgentToolLog, WorkflowEvent
from app.models.enums import ToolCallStatus

log = logging.getLogger("keicy.tools")

A = TypeVar("A", bound=BaseModel)
R = TypeVar("R", bound=BaseModel)


class ToolError(Exception):
    status: ToolCallStatus = ToolCallStatus.FAILED
    code = "tool_error"


class ToolPermissionError(ToolError):
    status = ToolCallStatus.BLOCKED
    code = "not_permitted"


class ToolLimitError(ToolError):
    status = ToolCallStatus.BLOCKED
    code = "tool_call_limit"


class ToolArgumentError(ToolError):
    code = "invalid_arguments"


class ToolResultError(ToolError):
    code = "invalid_result"


class ToolTimeoutError(ToolError):
    status = ToolCallStatus.TIMED_OUT
    code = "timeout"


class IdempotencyKeyRequired(ToolError):
    code = "idempotency_key_required"


class ToolNotImplementedError(ToolError):
    code = "not_implemented"


class UnknownToolError(ToolError):
    code = "unknown_tool"


class ToolBusinessError(ToolError):
    """Raised by handlers for expected domain failures (record not found, wrong request, ...)."""

    code = "business_rule"


@dataclass
class ToolContext:
    db: AsyncSession  # the agent's working session
    request_id: uuid.UUID
    agent_name: str
    agent_run_id: uuid.UUID
    allowed_tools: frozenset[str]
    max_tool_calls: int = 20
    calls_made: int = 0
    log_sessions: async_sessionmaker[AsyncSession] | None = None  # independent session for logs


Handler = Callable[[ToolContext, Any], Awaitable[BaseModel | dict[str, Any]]]


@dataclass
class Tool(Generic[A, R]):
    name: str
    description: str
    args_model: type[A]
    result_model: type[R]
    handler: Handler
    is_write: bool = False
    timeout_s: float = 15.0
    group: str = "general"

    def spec(self) -> dict[str, Any]:
        """Description an agent/LLM can be shown (name, purpose, argument schema)."""
        return {"name": self.name, "description": self.description, "write": self.is_write, "parameters": self.args_model.model_json_schema()}


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> Tool:
        if tool.name in self.tools:
            raise ValueError(f"Tool {tool.name!r} registered twice")
        self.tools[tool.name] = tool
        return tool

    def tool(self, name: str, *, args: type[BaseModel], result: type[BaseModel], description: str,
             is_write: bool = False, timeout_s: float = 15.0, group: str = "general"):
        def deco(fn: Handler) -> Handler:
            self.register(Tool(name, description, args, result, fn, is_write, timeout_s, group))
            return fn
        return deco

    def get(self, name: str) -> Tool:
        if name not in self.tools:
            raise UnknownToolError(f"No tool named {name!r}")
        return self.tools[name]

    def specs_for(self, allowed: frozenset[str]) -> list[dict[str, Any]]:
        return [t.spec() for n, t in sorted(self.tools.items()) if n in allowed]

    async def execute(self, ctx: ToolContext, name: str, args: dict[str, Any] | BaseModel | None = None,
                      *, idempotency_key: str | None = None) -> BaseModel:
        started = time.perf_counter()
        raw_args = args.model_dump(mode="json") if isinstance(args, BaseModel) else dict(args or {})
        tool: Tool | None = self.tools.get(name)
        is_write = bool(tool and tool.is_write)

        async def finish(status: ToolCallStatus, *, result: BaseModel | None = None, error: str | None = None, key: str | None = None):
            await self._log(ctx, name, is_write, raw_args, idempotency_key, status, result, error, key, _ms(started))

        # 1. permission + limits (checked before anything runs)
        if name not in ctx.allowed_tools:
            err = ToolPermissionError(f"Agent {ctx.agent_name!r} is not permitted to use tool {name!r}")
            await finish(err.status, error=str(err))
            raise err
        if tool is None:
            err = UnknownToolError(f"No tool named {name!r}")
            await finish(err.status, error=str(err))
            raise err
        if ctx.calls_made >= ctx.max_tool_calls:
            err = ToolLimitError(f"Agent {ctx.agent_name!r} reached its limit of {ctx.max_tool_calls} tool calls")
            await finish(err.status, error=str(err))
            raise err
        ctx.calls_made += 1

        # 2. arguments
        try:
            parsed = tool.args_model.model_validate(raw_args)
        except ValidationError as e:
            err = ToolArgumentError(f"Invalid arguments for {name}: {_short(e)}")
            await finish(err.status, error=str(err))
            raise err from e

        # 3. idempotency for writes
        if tool.is_write:
            if not idempotency_key:
                err = IdempotencyKeyRequired(f"Write tool {name!r} requires an idempotency_key")
                await finish(err.status, error=str(err))
                raise err
            previous = await self._previous_success(ctx, idempotency_key)
            if previous is not None:
                result = tool.result_model.model_validate(previous)
                await finish(ToolCallStatus.DUPLICATE, result=result)
                return result

        # 4. run
        try:
            out = await asyncio.wait_for(tool.handler(ctx, parsed), timeout=tool.timeout_s)
            result = out if isinstance(out, tool.result_model) else tool.result_model.model_validate(out)
            if tool.is_write:
                await ctx.db.commit()  # persist before recording the idempotency key
        except TimeoutError as e:
            await ctx.db.rollback()
            err = ToolTimeoutError(f"{name} did not finish within {tool.timeout_s}s")
            await finish(err.status, error=str(err))
            raise err from e
        except ToolError as e:
            await ctx.db.rollback()
            await finish(e.status, error=str(e))
            raise
        except ValidationError as e:
            await ctx.db.rollback()
            err = ToolResultError(f"{name} returned an invalid result: {_short(e)}")
            await finish(err.status, error=str(err))
            raise err from e
        except Exception as e:  # noqa: BLE001 — unexpected handler bug: log it, surface as ToolError
            await ctx.db.rollback()
            log.exception("Tool %s crashed", name)
            err = ToolError(f"{name} failed: {type(e).__name__}: {e}")
            await finish(err.status, error=str(err))
            raise err from e

        await finish(ToolCallStatus.SUCCEEDED, result=result, key=idempotency_key if tool.is_write else None)
        return result

    # ---------- persistence ----------

    async def _previous_success(self, ctx: ToolContext, key: str) -> dict[str, Any] | None:
        async with self._log_session(ctx) as db:
            row = await db.scalar(select(AgentToolLog.result).where(
                AgentToolLog.idempotency_key == key, AgentToolLog.status == ToolCallStatus.SUCCEEDED))
        return row

    def _log_session(self, ctx: ToolContext):
        if ctx.log_sessions is None:
            from app.db.session import SessionLocal

            return SessionLocal()
        return ctx.log_sessions()

    async def _log(self, ctx, name, is_write, raw_args, attempted_key, status, result, error, stored_key, duration_ms):
        # Only successful writes own the idempotency key (it is UNIQUE); failures keep it in arguments for tracing.
        arguments = {**raw_args, **({"_idempotency_key": attempted_key} if attempted_key else {})}
        result_json = result.model_dump(mode="json") if result is not None else None
        try:
            async with self._log_session(ctx) as db:
                db.add(AgentToolLog(agent_run_id=ctx.agent_run_id, tool_name=name, is_write=is_write, arguments=arguments,
                                    result=result_json, status=status, error=error, duration_ms=duration_ms, idempotency_key=stored_key))
                db.add(WorkflowEvent(request_id=ctx.request_id, event_type="agent.tool_called", payload={
                    "agent": ctx.agent_name, "agent_run_id": str(ctx.agent_run_id), "tool": name,
                    "status": status, "duration_ms": duration_ms, "error": error}))
                await db.commit()
        except Exception:  # noqa: BLE001 — logging failure must not change the tool outcome
            log.exception("Failed to log tool call %s", name)


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _short(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, x['loc'])) or 'root'}: {x['msg']}" for x in e.errors()[:6])
