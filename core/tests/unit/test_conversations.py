"""Conversation persistence + context restore (§29/§33)."""

from __future__ import annotations

from aizen.llm.base import MessageRole
from aizen.storage import ConversationStore
from aizen.tools.taint import taint_from_messages


def test_create_ensure_and_exists(db) -> None:
    store = ConversationStore(db)
    cid = store.create(title="first")
    assert store.exists(cid)
    assert store.ensure(cid) == cid
    assert store.ensure(None) != cid
    assert store.ensure(9999) != 9999


def test_history_roundtrips_roles_and_origins(db) -> None:
    store = ConversationStore(db)
    cid = store.create()
    store.add_message(cid, MessageRole.USER, "hello", origin="USER")
    store.add_message(cid, MessageRole.ASSISTANT, "hi", origin="SYSTEM")
    store.add_message(cid, MessageRole.SYSTEM, "ignored")
    history = store.history(cid)
    assert [m.role for m in history] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert [m.content for m in history] == ["hello", "hi"]
    assert history[0].origin == "USER"


def test_taint_is_recomputed_from_stored_messages(db) -> None:
    store = ConversationStore(db)
    cid = store.create()
    store.add_message(cid, MessageRole.USER, "fetch example.com")
    store.add_message(cid, MessageRole.ASSISTANT, "the page said x", origin="WEB", tainted=True)
    history = store.history(cid)
    assert history[-1].tainted is True
    assert taint_from_messages(history).is_tainted


def test_history_is_trimmed_to_the_last_n(db) -> None:
    store = ConversationStore(db)
    cid = store.create()
    for i in range(5):
        store.add_message(cid, MessageRole.USER, f"m{i}")
    history = store.history(cid, limit=2)
    assert [m.content for m in history] == ["m3", "m4"]


def test_private_flag(db) -> None:
    store = ConversationStore(db)
    cid = store.create(private=True)
    assert store.is_private(cid)
    store.set_private(cid, False)
    assert not store.is_private(cid)
