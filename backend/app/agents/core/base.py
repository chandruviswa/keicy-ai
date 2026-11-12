"""BaseAgent, AgentContext and AgentResult."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, TypeVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.core.tools import ToolContext, ToolRegistry
from app.agents.permissions import tools_for
from app.ai.gateway import Generation, LLMGateway, TextGeneration
from app.models.enums import RunStatus

T = TypeVar("T", bound=BaseModel)


@dataclass
class AgentContext:
    """Everything an agent may touch during one run. Agents reach the DB only through tools."""

    agent: BaseAgent
    request_id: uuid.UUID
    agent_run_id: uuid.UUID
    input: dict[str, Any]
    tool_ctx: ToolContext
    registry: ToolRegistry
    gateway: LLMGateway
    notes: list[str] = field(default_factory=list)  # free-form trace lines kept in the run output

    @property
    def db(self) -> AsyncSession:
        return self.tool_ctx.db

    async def tool(self, name: str, args: dict[str, Any] | BaseModel | None = None, *, idempotency_key: str | None = None):
        return await self.registry.execute(self.tool_ctx, name, args, idempotency_key=idempotency_key)

    async def generate(self, schema: type[T], prompt: str, *, purpose: str | None = None, max_tokens: int = 2048) -> Generation[T]:
        return await self.gateway.generate(schema, system=self.agent.system_prompt, prompt=prompt,
                                           purpose=purpose or self.agent.name, agent_run_id=self.agent_run_id, max_tokens=max_tokens)

    async def generate_text(self, prompt: str, *, purpose: str | None = None, max_tokens: int = 1024) -> TextGeneration:
        return await self.gateway.generate_text(system=self.agent.system_prompt, prompt=prompt,
                                                purpose=purpose or self.agent.name, agent_run_id=self.agent_run_id, max_tokens=max_tokens)

    def idempotency_key(self, operation: str) -> str:
        """Stable per request + operation, so re-running an agent can't duplicate a write."""
        return f"{self.request_id}:{self.agent.name}:{operation}"


@dataclass
class AgentResult:
    agent: str
    agent_run_id: uuid.UUID
    status: RunStatus
    output: dict[str, Any] | None
    error: str | None
    duration_ms: int
    tool_calls: int

    @property
    def ok(self) -> bool:
        return self.status == RunStatus.SUCCEEDED


class BaseAgent(ABC):
    name: ClassVar[str]
    description: ClassVar[str] = ""
    system_prompt: ClassVar[str] = "You are a careful procurement assistant."
    timeout_s: ClassVar[float] = 180.0
    max_tool_calls: ClassVar[int] = 20

    @property
    def allowed_tools(self) -> frozenset[str]:
        return tools_for(self.name)

    @abstractmethod
    async def run(self, ctx: AgentContext) -> BaseModel | dict[str, Any]:
        """Do the work via ctx.tool() and ctx.generate(); return a structured output."""
