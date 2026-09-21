from __future__ import annotations

from aizen.agent.context import ContextBuilder
from aizen.llm.base import Message, MessageRole


def test_build_structure() -> None:
    builder = ContextBuilder(max_tokens=1024)
    messages, breakdown = builder.build(user_text="hello", memories=["user likes tea"])
    assert messages[0].role is MessageRole.SYSTEM and messages[0].content
    assert messages[-1].role is MessageRole.USER and messages[-1].content == "hello"
    assert breakdown.used_tokens <= breakdown.budget_tokens


def test_memories_are_injected_once() -> None:
    builder = ContextBuilder(max_tokens=1024)
    _, breakdown = builder.build(user_text="hi", memories=["a", "b", "c"])
    assert breakdown.sections["memories"] > 0


def test_conversation_caps_at_budget() -> None:
    big = Message(role=MessageRole.USER, content="word " * 5000)
    builder = ContextBuilder(max_tokens=512)
    messages, breakdown = builder.build(user_text="now", conversation=[big, big])
    assert breakdown.used_tokens <= breakdown.budget_tokens
    # the budget is too small to hold both giant messages + user text
    assert len([m for m in messages if m.role in (MessageRole.USER, MessageRole.ASSISTANT)]) <= 3
