"""LLM provider interface: data models + the Protocol every adapter implements.

`chat_stream` yields a flat stream of events (tokens, tool calls, done) so the
agent loop can cancel between chunks and separate reasoning from speech.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from aizen.cancellation import CancellationToken


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = {}


class Message(BaseModel):
    role: MessageRole
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] | None = None
    # Provenance for context/taint bookkeeping; never serialized to providers.
    origin: str = "USER"
    tainted: bool = False


class ToolSchema(BaseModel):
    """JSON-schema view of a ToolSpec, as sent to the provider."""

    name: str
    description: str
    parameters: dict[str, Any] = {}


class GenOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: float = 0.1
    num_ctx: int = 4096
    max_tokens: int | None = None
    keep_alive: str = "30m"
    stop: list[str] | None = None


class Usage(BaseModel):
    # camelCase on the wire (promptTokens/completionTokens) to match §6.2.
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMEventKind(StrEnum):
    TOKEN = "token"
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    DONE = "done"
    ERROR = "error"


class LLMEvent(BaseModel):
    kind: LLMEventKind
    text: str | None = None
    tool_call: ToolCall | None = None
    usage: Usage | None = None
    error: str | None = None


class ModelInfo(BaseModel):
    name: str
    size_bytes: int | None = None
    family: str | None = None
    format: str | None = None


class ProviderHealth(BaseModel):
    ok: bool
    ms: float
    version: str | None = None
    models: list[ModelInfo] = []
    detail: str | None = None


class LLMProvider(ABC):
    """Swappable model server (Ollama, LM Studio, MLX-LM, remote vLLM, ...)."""

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSchema] | None = None,
        options: GenOptions,
        cancel: CancellationToken | None = None,
    ) -> AsyncIterator[LLMEvent]:
        """Stream chunks of the next assistant turn."""
        if False:  # pragma: no cover - unreachable; marks the async-generator shape
            yield LLMEvent(kind=LLMEventKind.DONE)

    @abstractmethod
    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]: ...

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]: ...

    @abstractmethod
    async def health(self) -> ProviderHealth: ...
