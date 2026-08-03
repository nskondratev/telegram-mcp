"""Parsing t.me links into a chat reference and a message id.

A pure function with no Telethon and no network: everything that decides *what*
was asked for lives here, so it can be tested on strings alone.
"""
from __future__ import annotations

import re

_LINK = re.compile(
    r"^https?://t\.me/"
    r"(?:c/(?P<internal>\d+)|(?P<username>[A-Za-z][A-Za-z0-9_]{3,31}))"
    r"/(?P<first>\d+)"
    r"(?:/(?P<second>\d+))?"
    r"/?(?:\?.*)?$"
)


def parse_message_link(url: str) -> tuple[int | str, int]:
    """t.me link -> (chat reference, message id).

    Private links (``t.me/c/<internal>/…``) yield the -100-prefixed chat id the
    allowlist stores. Public ones yield the @username, which the allowlist
    resolves the same way it resolves an alias or a title.

    Forum links carry one number more — ``t.me/c/<internal>/<topic>/<message>``.
    The topic itself is not needed to fetch a message, so it is dropped.
    """
    match = _LINK.match(str(url).strip())
    if not match:
        raise ValueError(
            f"Not a Telegram message link: {url!r}. "
            "Expected something like https://t.me/c/1234567890/8/4242 or https://t.me/name/123."
        )
    message_id = int(match.group("second") or match.group("first"))
    internal = match.group("internal")
    if internal:
        return int(f"-100{internal}"), message_id
    return match.group("username"), message_id
