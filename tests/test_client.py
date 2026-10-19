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


class RawClient:
    """Answers raw TL requests the way Telethon's ``client(request)`` does."""

    def __init__(self, documents=None, reactions_page=None, updates=None, peer_dialogs=None):
        self.documents = documents or []
        self.reactions_page = reactions_page
        self.updates = updates
        self.peer_dialogs = peer_dialogs
        self.requests = []

    async def __call__(self, request):
        from telethon.tl.functions.messages import (
            GetCustomEmojiDocumentsRequest,
            GetMessageReactionsListRequest,
            GetMessagesReactionsRequest,
            GetPeerDialogsRequest,
        )

        self.requests.append(request)
        if isinstance(request, GetCustomEmojiDocumentsRequest):
            return [d for d in self.documents if d.id in request.document_id]
        if isinstance(request, GetMessageReactionsListRequest):
            return self.reactions_page
        if isinstance(request, GetMessagesReactionsRequest):
            return self.updates
        if isinstance(request, GetPeerDialogsRequest):
            return self.peer_dialogs
        raise AssertionError(f"unexpected request {request!r}")


def custom_emoji_document(document_id, alt):
    from telethon.tl.types import Document, DocumentAttributeCustomEmoji, InputStickerSetEmpty

    return Document(
        id=document_id,
        access_hash=0,
        file_reference=b"",
        date=None,
        mime_type="application/x-tgsticker",
        size=0,
        dc_id=2,
        attributes=[DocumentAttributeCustomEmoji(alt=alt, stickerset=InputStickerSetEmpty())],
    )


class TestGetReactionPeers:
    def test_returns_the_users_and_chats_telegram_sends_with_the_reactions(self):
        from telethon.tl.types import Channel, ChatPhotoEmpty, Updates, User

        alice = User(id=42, first_name="Alice", min=True)
        group = Channel(id=7, title="Group", photo=ChatPhotoEmpty(), date=None)
        inner = RawClient(updates=Updates(updates=[], users=[alice], chats=[group], date=None, seq=0))
        result = asyncio.run(CachedClient(inner).get_reaction_peers("chat", [10, 9]))
        assert result == [alice, group]
        request = inner.requests[0]
        assert (request.peer, request.id) == ("chat", [10, 9])

    def test_an_answer_without_peers_is_an_empty_list(self):
        from telethon.tl.types import UpdatesTooLong

        inner = RawClient(updates=UpdatesTooLong())
        assert asyncio.run(CachedClient(inner).get_reaction_peers("chat", [10])) == []


class TestGetCustomEmoji:
    def test_maps_document_ids_to_their_emoji(self):
        inner = RawClient(documents=[custom_emoji_document(5000000000000000001, "🔥")])
        result = asyncio.run(CachedClient(inner).get_custom_emoji([5000000000000000001]))
        assert result == {5000000000000000001: "🔥"}

    def test_asks_telegram_only_for_ids_it_has_not_seen(self):
        inner = RawClient(
            documents=[
                custom_emoji_document(5000000000000000001, "🔥"),
                custom_emoji_document(5000000000000000002, "👍"),
            ]
        )
        client = CachedClient(inner)
        asyncio.run(client.get_custom_emoji([5000000000000000001]))
        result = asyncio.run(client.get_custom_emoji([5000000000000000001, 5000000000000000002]))
        assert result == {5000000000000000001: "🔥", 5000000000000000002: "👍"}
        assert [r.document_id for r in inner.requests] == [
            [5000000000000000001],
            [5000000000000000002],
        ]

    def test_nothing_to_resolve_means_no_request(self):
        inner = RawClient()
        assert asyncio.run(CachedClient(inner).get_custom_emoji([])) == {}
        assert inner.requests == []


class TestGetReactionsList:
    def test_requests_every_reaction_by_default(self):
        inner = RawClient(reactions_page="page")
        result = asyncio.run(
            CachedClient(inner).get_reactions_list("chat", 10, reaction=None, limit=50, offset=None)
        )
        assert result == "page"
        request = inner.requests[0]
        assert (request.peer, request.id, request.limit, request.reaction, request.offset) == (
            "chat",
            10,
            50,
            None,
            None,
        )

    def test_an_emoji_filter_becomes_a_standard_reaction(self):
        from telethon.tl.types import ReactionEmoji

        inner = RawClient(reactions_page="page")
        asyncio.run(
            CachedClient(inner).get_reactions_list("chat", 10, reaction="👀", limit=5, offset="abc")
        )
        request = inner.requests[0]
        assert request.reaction == ReactionEmoji(emoticon="👀")
        assert request.offset == "abc"

    def test_a_numeric_filter_becomes_a_custom_emoji(self):
        from telethon.tl.types import ReactionCustomEmoji

        inner = RawClient(reactions_page="page")
        asyncio.run(
            CachedClient(inner).get_reactions_list(
                "chat", 10, reaction="5000000000000000001", limit=5, offset=None
            )
        )
        assert inner.requests[0].reaction == ReactionCustomEmoji(document_id=5000000000000000001)

    def test_digits_that_are_not_ascii_are_not_a_custom_emoji_id(self):
        # "²".isdigit() is true, and int("²") raises.
        from telethon.tl.types import ReactionEmoji

        inner = RawClient(reactions_page="page")
        asyncio.run(CachedClient(inner).get_reactions_list("chat", 10, reaction="²", limit=5, offset=None))
        assert inner.requests[0].reaction == ReactionEmoji(emoticon="²")

    def test_a_custom_emoji_id_beyond_int64_is_a_clear_refusal(self):
        inner = RawClient(reactions_page="page")
        with pytest.raises(ValueError, match="custom_emoji_id"):
            asyncio.run(
                CachedClient(inner).get_reactions_list("chat", 10, reaction="9" * 20, limit=5, offset=None)
            )
        assert inner.requests == []



def peer_dialogs(*dialogs):
    from telethon.tl.types import updates
    from telethon.tl.types.messages import PeerDialogs

    state = updates.State(pts=0, qts=0, date=None, seq=0, unread_count=0)
    return PeerDialogs(dialogs=list(dialogs), messages=[], chats=[], users=[], state=state)


class TestGetPeerDialogs:
    def test_asks_for_the_dialogs_of_exactly_these_chats_in_one_request(self):
        from telethon.tl.types import Dialog, PeerChannel, PeerNotifySettings

        team = Dialog(
            peer=PeerChannel(channel_id=7),
            top_message=12,
            read_inbox_max_id=10,
            read_outbox_max_id=10,
            unread_count=2,
            unread_mentions_count=0,
            unread_reactions_count=0,
            unread_poll_votes_count=0,
            notify_settings=PeerNotifySettings(),
        )
        inner = RawClient(peer_dialogs=peer_dialogs(team))
        result = asyncio.run(CachedClient(inner).get_peer_dialogs(["team", "support"]))
        assert result == [team]
        # One look at the dialogs and nothing else: no read acknowledgement rides along.
        assert len(inner.requests) == 1
        assert inner.requests[0].peers == ["team", "support"]

    def test_an_answer_without_dialogs_is_an_empty_list(self):
        inner = RawClient(peer_dialogs=peer_dialogs())
        assert asyncio.run(CachedClient(inner).get_peer_dialogs(["team"])) == []
