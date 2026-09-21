"""Ollama adapter: native /api/chat, /api/embed, /api/tags, /api/version.

Supports streaming, tool calls (arguments may arrive as JSON strings), num_ctx
and keep_alive. Cancellation is checked between every chunk.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import httpx

from aizen.errors import ModelNotLoaded, ProviderUnavailable
from aizen.llm.base import (
    GenOptions,
    LLMEvent,
    LLMEventKind,
    LLMProvider,
    Message,
    MessageRole,
    ModelInfo,
    ProviderHealth,
    ToolCall,
    ToolSchema,
    Usage,
)

if TYPE_CHECKING:
    from aizen.agent.cancellation import CancellationToken


class OllamaProvider(LLMProvider):
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        *,
        model: str = "qwen3:4b",
        client: httpx.AsyncClient | None = None,
        default_timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = client or httpx.AsyncClient(timeout=default_timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- protocol implementation ---

    async def chat_stream(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSchema] | None = None,
        options: GenOptions,
        cancel: CancellationToken | None = None,
    ) -> AsyncIterator[LLMEvent]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_to_ollama(m) for m in messages],
            "stream": True,
            "options": {
                "temperature": options.temperature,
                "num_ctx": options.num_ctx,
            },
            "keep_alive": options.keep_alive,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": _function(t)} for t in tools]

        try:
            async with self._client.stream(
                "POST", f"{self.base_url}/api/chat", json=payload
            ) as resp:
                if resp.status_code == 404:
                    raise ModelNotLoaded(f"model {self.model} not present on this Ollama") from None
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise ProviderUnavailable(
                        f"Ollama /api/chat returned {resp.status_code}: {body[:200]!r}"
                    )
                async for line in resp.aiter_lines():
                    if cancel is not None:
                        cancel.check()
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    for ev in _chunk_events(chunk):
                        yield ev
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"Ollama unreachable at {self.base_url}: {exc}") from exc

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        try:
            resp = await self._client.post(
                f"{self.base_url}/api/embed",
                json={"model": model, "input": texts},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"Ollama embed failed: {exc}") from exc
        return [list(map(float, row)) for row in resp.json()["embeddings"]]

    async def list_models(self) -> list[ModelInfo]:
        try:
            resp = await self._client.get(f"{self.base_url}/api/tags")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"Ollama unreachable at {self.base_url}: {exc}") from exc
        return [
            ModelInfo(
                name=m["name"],
                size_bytes=m.get("size"),
                family=(m.get("details") or {}).get("family"),
                format=(m.get("details") or {}).get("format"),
            )
            for m in resp.json().get("models", [])
        ]

    async def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            resp = await self._client.get(f"{self.base_url}/api/version")
        except httpx.HTTPError as exc:
            return ProviderHealth(ok=False, ms=0.0, detail=str(exc))
        content = resp.json() if resp.status_code == 200 else {}
        return ProviderHealth(
            ok=resp.status_code == 200,
            ms=(time.perf_counter() - started) * 1000,
            version=content.get("version"),
        )


# --- serialization helpers ---


def _to_ollama(message: Message) -> dict[str, Any]:
    if message.role == MessageRole.USER:
        return {"role": "user", "content": message.content or ""}
    if message.role == MessageRole.SYSTEM:
        return {"role": "system", "content": message.content or ""}
    if message.role == MessageRole.TOOL:
        return {"role": "tool", "content": message.content or ""}
    outgoing: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
    if message.tool_calls:
        outgoing["tool_calls"] = [
            {
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments, default=str),
                }
            }
            for tc in message.tool_calls
        ]
    return outgoing


def _function(tool: ToolSchema) -> dict[str, Any]:
    return {"name": tool.name, "description": tool.description, "parameters": tool.parameters}


def _chunk_events(chunk: dict[str, Any]) -> list[LLMEvent]:
    events: list[LLMEvent] = []
    msg = chunk.get("message") or {}
    if reasoning := msg.get("reasoning_content"):
        events.append(LLMEvent(kind=LLMEventKind.REASONING, text=reasoning))
    if content := msg.get("content"):
        events.append(LLMEvent(kind=LLMEventKind.TOKEN, text=content))
    for raw in msg.get("tool_calls") or []:
        fn = raw.get("function") or {}
        arguments: dict[str, Any] = {}
        if isinstance(fn.get("arguments"), str):
            try:
                arguments = json.loads(fn["arguments"]) if fn["arguments"] else {}
            except json.JSONDecodeError:
                arguments = {}
        elif isinstance(fn.get("arguments"), dict):
            arguments = fn["arguments"]
        events.append(
            LLMEvent(
                kind=LLMEventKind.TOOL_CALL,
                tool_call=ToolCall(
                    id=raw.get("id") or fn.get("name", "tool_call"),
                    name=fn.get("name", ""),
                    arguments=arguments,
                ),
            )
        )
    if chunk.get("done"):
        events.append(
            LLMEvent(
                kind=LLMEventKind.DONE,
                usage=Usage(
                    prompt_tokens=chunk.get("prompt_eval_count", 0),
                    completion_tokens=chunk.get("eval_count", 0),
                ),
            )
        )
    return events
