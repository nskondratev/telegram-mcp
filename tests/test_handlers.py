"""Tests for reading Telegram: the allowlist holds on every single call.

The Telethon client is replaced with a fake — we assert both the result and the
fact that a forbidden chat never reaches the network.
"""
import asyncio
import datetime as dt

import pytest

from telegram_mcp import handlers
from telegram_mcp.core import AllowList, ChatEntry, ChatNotAllowed

TEAM = ChatEntry(alias="team", id=-1001111111111, title="Team chat")
SUPPORT = ChatEntry(alias="support", id=-1002222222222, title="Support")
PRIVATE_ID = -1009999999999


def allowlist():
    return AllowList([TEAM, SUPPORT])


class FakeEntity:
    def __init__(self, id, title, username=None, participants_count=None):
        self.id = id
        self.title = title
        self.username = username
        self.participants_count = participants_count


class FakeSender:
    def __init__(self, name, id=42):
        self.first_name = name
        self.last_name = None
        self.username = None
        self.id = id


class FakeFile:
    def __init__(self, size=1024, name="screenshot.png", ext=".png", mime_type="image/png", duration=None):
        self.size = size
        self.name = name
        self.ext = ext
        self.mime_type = mime_type
        self.duration = duration


class FakeMessage:
    def __init__(self, id, text, sender=None, reply_to_msg_id=None, date=None, file=None):
        self.id = id
        self.text = text
        self.message = text
        self.sender = sender
        self.sender_id = sender.id if sender else None
        self.reply_to_msg_id = reply_to_msg_id
        self.date = date or dt.datetime(2026, 7, 29, 9, 0, tzinfo=dt.timezone.utc)
        self.media = object() if file else None
        self.file = file


class FakeClient:
    """A minimal Telethon stand-in that remembers where it has been asked to go."""

    def __init__(self, entities=None, messages=None, dialogs=None):
        self.entities = entities or {}
        self.messages = messages or {}
        self.dialogs = dialogs or []
        self.calls = []

    async def get_entity(self, chat_id):
        self.calls.append(("get_entity", chat_id))
        if chat_id not in self.entities:
            raise ValueError(f"no entity {chat_id}")
        return self.entities[chat_id]

    async def get_messages(self, entity, **kwargs):
        self.calls.append(("get_messages", entity.id, kwargs))
        found = self.messages.get(entity.id, [])
        search = kwargs.get("search")
        if search:
            found = [m for m in found if search.lower() in (m.text or "").lower()]
        limit = kwargs.get("limit")
        return found[:limit] if limit else found


def work_client():
    return FakeClient(
        entities={
            TEAM.id: FakeEntity(TEAM.id, "Team chat", username="team_chat"),
            SUPPORT.id: FakeEntity(SUPPORT.id, "Support"),
            PRIVATE_ID: FakeEntity(PRIVATE_ID, "Private"),
        },
        messages={
            TEAM.id: [
                FakeMessage(10, "Grafana: login is pinned", sender=FakeSender("Alice")),
                FakeMessage(9, "ArgoCD access granted", sender=FakeSender("Bob")),
            ],
            SUPPORT.id: [FakeMessage(5, "A customer complains about billing", sender=FakeSender("Carol"))],
            PRIVATE_ID: [FakeMessage(1, "private message", sender=FakeSender("Dave"))],
        },
    )


class TestGetMessages:
    def test_reads_messages_from_allowed_chat(self):
        client = work_client()
        result = asyncio.run(handlers.get_messages(client, allowlist(), "team", limit=10))
        assert [m["id"] for m in result["messages"]] == [10, 9]
        assert result["messages"][0]["text"] == "Grafana: login is pinned"
        assert result["messages"][0]["sender"] == "Alice"
        assert result["chat"]["alias"] == "team"

    def test_denies_chat_outside_allowlist_without_touching_network(self):
        client = work_client()
        with pytest.raises(ChatNotAllowed):
            asyncio.run(handlers.get_messages(client, allowlist(), PRIVATE_ID, limit=10))
        assert client.calls == []

    def test_denies_unknown_alias_without_touching_network(self):
        client = work_client()
        with pytest.raises(ChatNotAllowed):
            asyncio.run(handlers.get_messages(client, allowlist(), "family", limit=10))
        assert client.calls == []

    def test_caps_limit_to_protect_context(self):
        client = work_client()
        asyncio.run(handlers.get_messages(client, allowlist(), "team", limit=100000))
        call = [c for c in client.calls if c[0] == "get_messages"][0]
        assert call[2]["limit"] <= handlers.MAX_LIMIT

    def test_sanitizes_message_text(self):
        client = work_client()
        client.messages[TEAM.id] = [FakeMessage(11, "he\u200bllo\x00", sender=FakeSender("Bob"))]
        result = asyncio.run(handlers.get_messages(client, allowlist(), "team"))
        assert result["messages"][0]["text"] == "hello"

    def test_rejects_entity_whose_real_id_is_not_allowed(self):
        """Anti-spoofing: the entity came back with someone else's id, so it must not be served."""
        client = work_client()
        client.entities[TEAM.id] = FakeEntity(PRIVATE_ID, "Private")
        with pytest.raises(ChatNotAllowed):
            asyncio.run(handlers.get_messages(client, allowlist(), "team"))


class FakeUser:
    """A direct chat: a user has no title, but has a first and last name."""

    def __init__(self, id, first_name, last_name=None, username=None):
        self.id = id
        self.first_name = first_name
        self.last_name = last_name
        self.username = username


class TestDisplayNames:
    def test_list_chats_shows_person_name_for_direct_chat(self):
        client = work_client()
        client.entities[SUPPORT.id] = FakeUser(SUPPORT.id, "Alice", "Smith", username="alice")
        result = asyncio.run(handlers.list_chats(client, allowlist()))
        support = next(c for c in result["chats"] if c["alias"] == "support")
        assert support["title"] == "Alice Smith"

    def test_chat_info_shows_person_name_for_direct_chat(self):
        client = work_client()
        client.entities[SUPPORT.id] = FakeUser(SUPPORT.id, "Alice", "Smith")
        result = asyncio.run(handlers.get_chat_info(client, allowlist(), "support"))
        assert result["title"] == "Alice Smith"

    def test_messages_report_real_chat_title_not_the_config_one(self):
        """The chat was renamed in Telegram — report the new title, not the config one."""
        client = work_client()
        client.entities[TEAM.id] = FakeEntity(TEAM.id, "Team chat 2.0")
        result = asyncio.run(handlers.get_messages(client, allowlist(), "team", limit=2))
        assert result["chat"]["title"] == "Team chat 2.0"

    def test_message_context_reports_real_chat_title(self):
        client = work_client()
        client.entities[TEAM.id] = FakeEntity(TEAM.id, "Team chat 2.0")
        result = asyncio.run(
            handlers.get_message_context(client, allowlist(), "team", message_id=10, around=2)
        )
        assert result["chat"]["title"] == "Team chat 2.0"

    def test_search_results_report_real_chat_title(self):
        client = work_client()
        client.entities[TEAM.id] = FakeEntity(TEAM.id, "Team chat 2.0")
        result = asyncio.run(handlers.search_messages(client, allowlist(), "ArgoCD", chat="team"))
        assert result["messages"][0]["chat_title"] == "Team chat 2.0"


class TestListChats:
    def test_lists_only_allowed_chats(self):
        client = work_client()
        result = asyncio.run(handlers.list_chats(client, allowlist()))
        assert {c["alias"] for c in result["chats"]} == {"team", "support"}

    def test_never_queries_chats_outside_allowlist(self):
        client = work_client()
        asyncio.run(handlers.list_chats(client, allowlist()))
        queried = {c[1] for c in client.calls if c[0] == "get_entity"}
        assert PRIVATE_ID not in queried

    def test_survives_unavailable_chat(self):
        """A chat was deleted or the account was kicked — the rest is still served."""
        client = work_client()
        del client.entities[SUPPORT.id]
        result = asyncio.run(handlers.list_chats(client, allowlist()))
        aliases = {c["alias"]: c for c in result["chats"]}
        assert aliases["team"]["title"] == "Team chat"
        assert "error" in aliases["support"]


class TestSearchMessages:
    def test_searches_inside_allowed_chat(self):
        client = work_client()
        result = asyncio.run(handlers.search_messages(client, allowlist(), "ArgoCD", chat="team"))
        assert [m["id"] for m in result["messages"]] == [9]

    def test_searches_across_all_allowed_chats_when_chat_is_omitted(self):
        client = work_client()
        result = asyncio.run(handlers.search_messages(client, allowlist(), "a"))
        chats = {m["chat_alias"] for m in result["messages"]}
        assert chats == {"team", "support"}

    def test_never_searches_outside_allowlist(self):
        client = work_client()
        asyncio.run(handlers.search_messages(client, allowlist(), "private"))
        searched = {c[1] for c in client.calls if c[0] == "get_messages"}
        assert PRIVATE_ID not in searched

    def test_denies_explicit_forbidden_chat(self):
        client = work_client()
        with pytest.raises(ChatNotAllowed):
            asyncio.run(handlers.search_messages(client, allowlist(), "anything", chat=PRIVATE_ID))
        assert client.calls == []


class TestGetChatInfo:
    def test_returns_metadata_for_allowed_chat(self):
        client = work_client()
        result = asyncio.run(handlers.get_chat_info(client, allowlist(), "team"))
        assert result["id"] == TEAM.id
        assert result["title"] == "Team chat"
        assert result["username"] == "team_chat"

    def test_denies_chat_outside_allowlist(self):
        client = work_client()
        with pytest.raises(ChatNotAllowed):
            asyncio.run(handlers.get_chat_info(client, allowlist(), PRIVATE_ID))
        assert client.calls == []


class TestGetMessageContext:
    def test_returns_messages_around_target(self):
        client = work_client()
        result = asyncio.run(
            handlers.get_message_context(client, allowlist(), "team", message_id=10, around=2)
        )
        assert [m["id"] for m in result["messages"]] == [10, 9]

    def test_denies_chat_outside_allowlist(self):
        client = work_client()
        with pytest.raises(ChatNotAllowed):
            asyncio.run(
                handlers.get_message_context(client, allowlist(), PRIVATE_ID, message_id=1, around=2)
            )
        assert client.calls == []


class TestAttachmentMetadata:
    def test_message_without_media_reports_no_file(self):
        message = FakeMessage(1, "just text")
        assert handlers.file_info(message) is None

    def test_message_with_media_reports_what_is_worth_fetching(self):
        message = FakeMessage(1, "look", file=FakeFile(size=148213))
        assert handlers.file_info(message) == {
            "size": 148213,
            "name": "screenshot.png",
            "ext": ".png",
            "mime": "image/png",
            "duration": None,
        }

    def test_read_tools_expose_the_file_field(self):
        message = FakeMessage(7, "look", file=FakeFile())
        assert handlers._message_to_dict(message)["file"]["mime"] == "image/png"
        assert handlers._message_to_dict(FakeMessage(8, "text"))["file"] is None
