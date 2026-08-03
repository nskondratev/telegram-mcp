"""Tests for parsing t.me message links."""
import pytest

from telegram_mcp.links import parse_message_link


class TestPrivateLinks:
    def test_forum_topic_link(self):
        assert parse_message_link("https://t.me/c/1234567890/8/4242") == (-1001234567890, 4242)

    def test_link_without_topic(self):
        assert parse_message_link("https://t.me/c/1234567890/4242") == (-1001234567890, 4242)

    def test_trailing_slash_and_query(self):
        assert parse_message_link("https://t.me/c/1234567890/8/4242/?single") == (-1001234567890, 4242)


class TestPublicLinks:
    def test_username_link(self):
        assert parse_message_link("https://t.me/durov/123") == ("durov", 123)

    def test_username_link_with_topic(self):
        assert parse_message_link("https://t.me/somegroup/8/123") == ("somegroup", 123)

    def test_http_scheme_is_accepted(self):
        assert parse_message_link("http://t.me/durov/123") == ("durov", 123)

    def test_surrounding_whitespace_is_ignored(self):
        assert parse_message_link("  https://t.me/durov/123  ") == ("durov", 123)


class TestRejects:
    @pytest.mark.parametrize(
        "value",
        [
            "not a link",
            "https://example.com/c/1/2",
            "https://t.me/c/abc/1",
            "https://t.me/durov",
            "https://t.me/c/1234567890",
            "",
        ],
    )
    def test_rejects_garbage(self, value):
        with pytest.raises(ValueError):
            parse_message_link(value)
