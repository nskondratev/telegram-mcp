"""Thin wrapper around the Telethon client.

Its only job is to work around a known Telethon quirk: ``get_entity`` by id only
succeeds for entities already present in the session cache. The first miss warms
the cache via ``get_dialogs()`` and retries. The dialogs themselves are never
returned anywhere — only allowlisted chats ever leave this process.
"""
from __future__ import annotations


class CachedClient:
    def __init__(self, inner):
        self._inner = inner
        self._warmed = False

    async def get_entity(self, chat_id):
        try:
            return await self._inner.get_entity(chat_id)
        except Exception:
            if self._warmed:
                raise
            self._warmed = True
            await self._inner.get_dialogs()
            return await self._inner.get_entity(chat_id)

    async def get_messages(self, entity, **kwargs):
        return await self._inner.get_messages(entity, **kwargs)
