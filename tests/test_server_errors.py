"""A refusal must reach the model; a crash must not leak what went wrong."""
import asyncio
import inspect

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from telegram_mcp.core import ChatNotAllowed
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
            raise RuntimeError("session string is invalid")

        # Not a refusal: the SDK logs it and tells the model nothing, which is
        # the behaviour we want to keep.
        with pytest.raises(RuntimeError):
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
