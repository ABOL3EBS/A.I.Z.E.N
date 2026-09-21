"""OpenAI-compatible adapter: /v1/chat/completions + /v1/embeddings + /v1/models.

Targets Ollama's /v1 endpoint, LM Studio, llama.cpp server, MLX-LM and remote
vLLM. Tool-call deltas arrive fragmented; we aggregate per index before
yielding a TOOL_CALL event.
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
from aizen.llm.ollama import _function

if TYPE_CHECKING:
    from aizen.agent.cancellation import CancellationToken


class OpenAICompatProvider(LLMProvider):
    def __init__(
        self,
        base_url: str,
        *,
        model: str,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        default_timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = client or httpx.AsyncClient(timeout=default_timeout, headers=headers)

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
            "messages": [_to_openai(m) for m in messages],
            "stream": True,
            "temperature": options.temperature,
            "max_tokens": options.max_tokens,
            "stream_options": {"include_usage": True},
        }
        if options.stop:
            payload["stop"] = options.stop
        if tools:
            payload["tools"] = [{"type": "function", "function": _function(t)} for t in tools]

        try:
            async with self._client.stream(
                "POST", f"{self.base_url}/v1/chat/completions", json=payload
            ) as resp:
                if resp.status_code == 404:
                    raise ModelNotLoaded(
                        f"model {self.model} not found at {self.base_url}"
                    ) from None
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise ProviderUnavailable(
                        f"OpenAI-compatible endpoint returned {resp.status_code}: {body[:200]!r}"
                    )
                usage: dict[str, int] = {}
                calls: dict[int, dict[str, Any]] = {}
                async for line in resp.aiter_lines():
                    if cancel is not None:
                        cancel.check()
                    for ev in _sse_events(line, calls, usage):
                        yield ev
                for ev in _finalize_calls(calls):
                    yield ev
                yield LLMEvent(
                    kind=LLMEventKind.DONE,
                    usage=Usage(
                        prompt_tokens=usage.get("prompt", 0),
                        completion_tokens=usage.get("completion", 0),
                    ),
                )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"endpoint unreachable at {self.base_url}: {exc}") from exc

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        try:
            resp = await self._client.post(
                f"{self.base_url}/v1/embeddings",
                json={"model": model, "input": texts},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"embeddings failed: {exc}") from exc
        data = resp.json()["data"]
        data.sort(key=lambda item: item["index"])
        return [list(map(float, item["embedding"])) for item in data]

    async def list_models(self) -> list[ModelInfo]:
        try:
            resp = await self._client.get(f"{self.base_url}/v1/models")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"endpoint unreachable at {self.base_url}: {exc}") from exc
        return [ModelInfo(name=item["id"]) for item in resp.json().get("data", [])]

    async def health(self) -> ProviderHealth:
        models: list[ModelInfo] = []
        started = time.perf_counter()
        try:
            resp = await self._client.get(f"{self.base_url}/v1/models")
        except httpx.HTTPError as exc:
            return ProviderHealth(ok=False, ms=0.0, detail=str(exc))
        if resp.status_code == 200:
            models = [ModelInfo(name=item["id"]) for item in resp.json().get("data", [])]
        return ProviderHealth(
            ok=resp.status_code == 200,
            ms=(time.perf_counter() - started) * 1000,
            models=models,
        )


# --- OpenAI message shape ---


def _to_openai(message: Message) -> dict[str, Any]:
    if message.role == MessageRole.TOOL:
        return {
            "role": "tool",
            "content": message.content or "",
            "tool_call_id": message.tool_call_id or "",
        }
    outgoing: dict[str, Any] = {"role": message.role.value, "content": message.content or ""}
    if message.tool_calls:
        outgoing["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, default=str)},
            }
            for tc in message.tool_calls
        ]
    return outgoing


def _sse_events(
    line: str,
    calls: dict[int, dict[str, Any]],
    usage: dict[str, int],
) -> list[LLMEvent]:
    if not line.startswith("data:"):
        return []
    data = line[len("data:") :].strip()
    if data in ("", "[DONE]"):
        return []

    chunk = json.loads(data)
    events: list[LLMEvent] = []

    if usage_raw := chunk.get("usage"):
        usage.clear()
        usage.update(
            {
                "prompt": usage_raw.get("prompt_tokens", 0),
                "completion": usage_raw.get("completion_tokens", 0),
            }
        )

    for choice in chunk.get("choices", []):
        delta = choice.get("delta", {})
        if content := delta.get("content"):
            events.append(LLMEvent(kind=LLMEventKind.TOKEN, text=content))
        for raw in delta.get("tool_calls") or []:
            index = raw.get("index", 0)
            slot = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if raw.get("id"):
                slot["id"] = raw["id"]
            fn = raw.get("function") or {}
            if fn.get("name"):
                slot["name"] = fn["name"]
            slot["arguments"] += fn.get("arguments") or ""
    return events


def _finalize_calls(calls: dict[int, dict[str, Any]]) -> list[LLMEvent]:
    events: list[LLMEvent] = []
    for slot in sorted(calls.values(), key=lambda s: s["id"]):
        arguments: dict[str, Any] = {}
        if slot["arguments"]:
            try:
                arguments = json.loads(slot["arguments"])
            except json.JSONDecodeError:
                arguments = {"_raw": slot["arguments"]}
        events.append(
            LLMEvent(
                kind=LLMEventKind.TOOL_CALL,
                tool_call=ToolCall(
                    id=slot["id"] or slot["name"],
                    name=slot["name"],
                    arguments=arguments,
                ),
            )
        )
    return events
