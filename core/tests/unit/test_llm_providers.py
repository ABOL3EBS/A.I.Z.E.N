from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from aizen.llm.base import GenOptions, LLMEventKind, Message, MessageRole
from aizen.llm.ollama import OllamaProvider
from aizen.llm.openai_compat import OpenAICompatProvider

CHAT_LINES = (
    '{"message":{"role":"assistant","content":"The result is "},"done":false}\n'
    '{"message":{"role":"assistant","content":"50"},"done":false}\n'
    '{"message":{"role":"assistant","tool_calls":[{"function":{"name":"system.calculate",'
    '"arguments":"{\\"expression\\": \\"40 + 10\\"}"}}]},"done":false}\n'
    '{"done":true,"prompt_eval_count":7,"eval_count":3}\n'
)


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/chat":
        return httpx.Response(200, text=CHAT_LINES)
    if path == "/api/embed":
        body = json.loads(request.content)
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2] for _ in body["input"]]})
    if path == "/api/tags":
        return httpx.Response(
            200,
            json={"models": [{"name": "qwen3:4b", "size": 100, "details": {"family": "qwen3"}}]},
        )
    return httpx.Response(200, json={"version": "0.5.4"})


@pytest.fixture
async def ollama() -> AsyncIterator[OllamaProvider]:
    transport = httpx.MockTransport(_handler)
    provider = OllamaProvider(
        base_url="http://ollama.test",
        model="qwen3:4b",
        client=httpx.AsyncClient(transport=transport),
    )
    yield provider
    await provider.aclose()


async def test_chat_stream_parses_tokens_done(ollama: OllamaProvider) -> None:
    events = [
        ev
        async for ev in ollama.chat_stream(
            [Message(role=MessageRole.USER, content="what is 40+10")],
            options=GenOptions(num_ctx=1024),
        )
    ]
    kinds = [ev.kind for ev in events]
    assert kinds[0] is LLMEventKind.TOKEN
    assert kinds[-1] is LLMEventKind.DONE
    text = "".join(ev.text or "" for ev in events if ev.text)
    assert text == "The result is 50"
    assert events[-1].usage is not None
    assert events[-1].usage.prompt_tokens == 7
    assert events[-1].usage.completion_tokens == 3


async def test_chat_stream_parses_tool_call(ollama: OllamaProvider) -> None:
    events = [
        ev
        async for ev in ollama.chat_stream(
            [Message(role=MessageRole.USER, content="40+10")],
            options=GenOptions(num_ctx=1024),
        )
    ]
    tool = next(ev.tool_call for ev in events if ev.kind is LLMEventKind.TOOL_CALL and ev.tool_call)
    assert tool.name == "system.calculate"
    assert tool.arguments == {"expression": "40 + 10"}


async def test_embed_and_list_models(ollama: OllamaProvider) -> None:
    vectors = await ollama.embed(["hello"], model="nomic-embed-text")
    assert vectors == [[0.1, 0.2]]
    models = await ollama.list_models()
    assert models[0].name == "qwen3:4b"
    assert models[0].family == "qwen3"


async def test_health(ollama: OllamaProvider) -> None:
    health = await ollama.health()
    assert health.ok
    assert health.version == "0.5.4"


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n"


OPENAI_LINES = "".join(
    [
        _sse({"choices": [{"delta": {"role": "assistant", "content": "You owe "}}]}),
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "function": {
                                        "name": "system.calculate",
                                        "arguments": '{"exp',
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ),
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {
                                        "arguments": 'ression": "12"}',
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ),
        _sse({"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 5}}),
        "data: [DONE]\n",
    ]
)


def _openai_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/chat/completions":
        return httpx.Response(200, text=OPENAI_LINES)
    if path == "/v1/models":
        return httpx.Response(200, json={"data": [{"id": "qwen3:4b"}]})
    return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.5]}]})


@pytest.fixture
async def openai() -> AsyncIterator[OpenAICompatProvider]:
    transport = httpx.MockTransport(_openai_handler)
    provider = OpenAICompatProvider(
        base_url="http://lm.test",
        model="qwen3:4b",
        client=httpx.AsyncClient(transport=transport),
    )
    yield provider
    await provider.aclose()


async def test_openai_stream_aggregates_tool_calls(openai: OpenAICompatProvider) -> None:
    events = [
        ev
        async for ev in openai.chat_stream(
            [Message(role=MessageRole.USER, content="hi")],
            options=GenOptions(num_ctx=1024),
        )
    ]
    tool = next(ev.tool_call for ev in events if ev.kind is LLMEventKind.TOOL_CALL and ev.tool_call)
    assert tool.id == "call_1"
    assert tool.name == "system.calculate"
    assert tool.arguments == {"expression": "12"}
    text = "".join(ev.text or "" for ev in events if ev.text)
    assert text == "You owe "


async def test_openai_embed_and_models(openai: OpenAICompatProvider) -> None:
    assert await openai.embed(["x"], model="m") == [[0.5]]
    assert (await openai.list_models())[0].name == "qwen3:4b"
