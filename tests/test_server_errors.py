"""A refusal must reach the model; a crash must not leak what went wrong."""
import asyncio
import inspect

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from telegram_mcp import server
from telegram_mcp.core import ChatNotAllowed, MediaTooLarge, NotConfigured
from telegram_mcp.server import _anticipated, _get_client


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


class TestGetClientMisconfiguration:
    def test_raises_not_configured_when_credentials_are_missing(self, monkeypatch):
        monkeypatch.setattr(server, "_client", None)
        monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
        monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
        monkeypatch.delenv("TELEGRAM_SESSION_STRING", raising=False)

        # The exact type matters: it is what lets `_anticipated` turn this into
        # a refusal the model can act on, instead of an opaque crash.
        with pytest.raises(NotConfigured):
            asyncio.run(_get_client())

    # Looks like an api_hash pasted into the api_id slot: the likeliest typo.
    MISPLACED_HASH = "0123456789abcdef0123456789abcdef"

    def _misplaced_api_id(self, monkeypatch):
        monkeypatch.setattr(server, "_client", None)
        monkeypatch.setenv("TELEGRAM_API_ID", self.MISPLACED_HASH)
        monkeypatch.setenv("TELEGRAM_API_HASH", "fake-api-hash")
        monkeypatch.setenv("TELEGRAM_SESSION_STRING", "fake-session")
        # Stand-ins so that nothing but the api_id can fail, and nothing
        # reaches the network: a fake session string would otherwise be
        # rejected first, before the api_id is ever read.
        monkeypatch.setattr(server, "StringSession", lambda string: object())

        def refuse_to_connect(*args, **kwargs):
            raise AssertionError("must not build a client from a malformed api_id")

        monkeypatch.setattr(server, "TelegramClient", refuse_to_connect)

    def test_non_numeric_api_id_is_not_configured_and_not_echoed(self, monkeypatch):
        self._misplaced_api_id(monkeypatch)

        with pytest.raises(NotConfigured) as caught:
            asyncio.run(_get_client())
        assert "TELEGRAM_API_ID" in str(caught.value)
        assert self.MISPLACED_HASH not in str(caught.value)
        # int() quotes the value in its own message: that error must not ride
        # along as the cause or the displayed context of the refusal.
        assert caught.value.__cause__ is None
        assert caught.value.__suppress_context__

    def test_the_refusal_the_model_reads_does_not_carry_the_value(self, monkeypatch):
        # A bare ValueError is an anticipated refusal, so its text would reach
        # the model and end up in the conversation transcript.
        self._misplaced_api_id(monkeypatch)

        @_anticipated
        async def tool():
            await _get_client()

        with pytest.raises(ToolError) as caught:
            asyncio.run(tool())
        assert "TELEGRAM_API_ID" in str(caught.value)
        assert self.MISPLACED_HASH not in str(caught.value)
