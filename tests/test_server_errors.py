"""A refusal must reach the model; a crash must not leak what went wrong."""
import asyncio
import inspect

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from telegram_mcp.core import ChatNotAllowed, MediaTooLarge, NotConfigured
from telegram_mcp.server import _anticipated


class TestAnticipated:
    def test_refusal_keeps_its_text(self):
        @_anticipated
        async def tool():
            raise ChatNotAllowed("chat -1009999999999 is not in the allowlist")

        with pytest.raises(ToolError, match="not in the allowlist"):
            asyncio.run(tool())

    def test_value_error_is_a_refusal_too(self):
        @_anticipated
        async def tool():
            raise ValueError("Message 7 not found in 'team'.")

        with pytest.raises(ToolError, match="not found"):
            asyncio.run(tool())

    def test_a_crash_is_left_to_the_sdk(self):
        @_anticipated
        async def tool():
            raise KeyError("entities")

        # Not a refusal: the SDK logs it and tells the model nothing, which is
        # the behaviour we want to keep.
        with pytest.raises(KeyError):
            asyncio.run(tool())

    def test_not_configured_reaches_the_model(self):
        @_anticipated
        async def tool():
            raise NotConfigured(
                "The session string is invalid (revoked or expired) — "
                "issue a new one with `telegram-mcp login`."
            )

        with pytest.raises(ToolError, match="telegram-mcp login"):
            asyncio.run(tool())

    def test_the_signature_survives_decoration(self):
        # The SDK builds the input schema from the signature: losing it would
        # silently turn every argument into an untyped one.
        @_anticipated
        async def tool(chat: str, limit: int = 50) -> dict:
            return {}

        assert list(inspect.signature(tool).parameters) == ["chat", "limit"]

    def test_the_result_passes_through_untouched(self):
        @_anticipated
        async def tool():
            return {"chats": []}

        assert asyncio.run(tool()) == {"chats": []}

    def test_too_large_keeps_its_hint(self):
        @_anticipated
        async def tool():
            raise MediaTooLarge("above the max_size limit of 50000000 bytes")

        # The hint is the whole point of the refusal: without it the model
        # cannot know that a bigger max_size would work.
        with pytest.raises(ToolError, match="max_size"):
            asyncio.run(tool())
