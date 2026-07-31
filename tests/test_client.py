"""Tests for the Telethon wrapper: warming the dialog cache on an unknown entity."""
import asyncio

import pytest

from telegram_mcp.client import CachedClient


class FlakyClient:
    """Telethon does not know an entity by id until get_dialogs runs — typical behaviour."""

    def __init__(self, fail_times=1):
        self.fail_times = fail_times
        self.calls = []

    async def get_entity(self, chat_id):
        self.calls.append(("get_entity", chat_id))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ValueError("Could not find the input entity")
        return {"id": chat_id}

    async def get_dialogs(self, **kwargs):
        self.calls.append(("get_dialogs", kwargs))
        return []

    async def get_messages(self, entity, **kwargs):
        self.calls.append(("get_messages", entity, kwargs))
        return ["msg"]


def test_retries_get_entity_after_warming_dialog_cache():
    inner = FlakyClient(fail_times=1)
    result = asyncio.run(CachedClient(inner).get_entity(-100123))
    assert result == {"id": -100123}
    assert [c[0] for c in inner.calls] == ["get_entity", "get_dialogs", "get_entity"]


def test_warms_dialog_cache_only_once():
    inner = FlakyClient(fail_times=1)
    client = CachedClient(inner)
    asyncio.run(client.get_entity(-100123))
    inner.fail_times = 1
    with pytest.raises(ValueError):
        asyncio.run(client.get_entity(-100456))
    assert [c[0] for c in inner.calls].count("get_dialogs") == 1


def test_propagates_error_when_entity_stays_unknown():
    inner = FlakyClient(fail_times=2)
    with pytest.raises(ValueError):
        asyncio.run(CachedClient(inner).get_entity(-100123))


def test_delegates_get_messages():
    inner = FlakyClient(fail_times=0)
    result = asyncio.run(CachedClient(inner).get_messages({"id": 1}, limit=5))
    assert result == ["msg"]
    assert inner.calls == [("get_messages", {"id": 1}, {"limit": 5})]
