"""The text of a rich message, which Telegram keeps in blocks instead of Message.message.

Since layer 229 a message can be written with rich formatting: headings, lists,
code blocks, tables. Such a message arrives with an empty ``message`` and the
whole text in ``rich_message`` — the same PageBlock and RichText types Instant
View pages are made of. Telethon parses them but its ``Message.text`` only reads
``message``, so without this module the text is lost.

Inline formatting follows Telethon's default markdown, the form every other
message text comes in. Types are told apart by class name and read through
getattr, the way the rest of the server reads Telegram objects; a block type this
module does not know still gives up whatever text, blocks or items it carries.
"""
from __future__ import annotations

#: Inline wrappers that Telethon's markdown has a delimiter for.
_DELIMITERS = {
    "TextBold": "**",
    "TextItalic": "__",
    "TextStrike": "~~",
    "TextFixed": "`",
}

#: Blocks that are a picture, a file or an embed: their caption is all the text they have.
_MEDIA_BLOCKS = {
    "PageBlockPhoto": "photo",
    "PageBlockVideo": "video",
    "PageBlockAudio": "audio",
    "PageBlockDocument": "document",
    "PageBlockCollage": "collage",
    "PageBlockSlideshow": "slideshow",
    "PageBlockEmbed": "embed",
    "PageBlockEmbedPost": "embedded post",
    "PageBlockMap": "map",
}


def rich_text(text) -> str:
    """One RichText tree as markdown."""
    if text is None:
        return ""
    if isinstance(text, str):
        return text
    name = type(text).__name__
    if name == "TextEmpty":
        return ""
    if name == "TextConcat":
        return "".join(rich_text(part) for part in getattr(text, "texts", None) or [])
    if name == "TextCustomEmoji":
        return getattr(text, "alt", None) or ""
    if name == "TextMath":
        return f"`{getattr(text, 'source', '')}`"
    inner = rich_text(getattr(text, "text", None))
    if name in _DELIMITERS and inner:
        delimiter = _DELIMITERS[name]
        return f"{delimiter}{inner}{delimiter}"
    if name == "TextUrl" and getattr(text, "url", None):
        return f"[{inner}]({text.url})"
    return inner


def _caption(caption) -> str:
    """A PageCaption: its text, then who to credit."""
    if caption is None:
        return ""
    if type(caption).__name__ != "PageCaption":
        return rich_text(caption)
    text, credit = rich_text(getattr(caption, "text", None)), rich_text(getattr(caption, "credit", None))
    return " — ".join(part for part in (text, credit) if part)


def _indent(text: str, first: str, rest: str) -> str:
    lines = text.split("\n")
    return "\n".join([first + lines[0], *(rest + line if line else line for line in lines[1:])])


def _list_item(item, marker: str) -> str:
    if getattr(item, "checkbox", None):
        marker += "[x] " if getattr(item, "checked", None) else "[ ] "
    blocks = getattr(item, "blocks", None)
    body = _blocks(blocks) if blocks is not None else rich_text(getattr(item, "text", None))
    return _indent(body, marker, " " * len(marker))


def _ordered_number(item, position: int) -> str:
    num = getattr(item, "num", None)
    if num:
        return str(num)
    value = getattr(item, "value", None)
    return str(value if value is not None else position)


def _table(block) -> str:
    lines = []
    title = rich_text(getattr(block, "title", None))
    if title:
        lines.append(title)
    for row in getattr(block, "rows", None) or []:
        cells = [rich_text(getattr(cell, "text", None)) for cell in getattr(row, "cells", None) or []]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _block(block) -> str:
    name = type(block).__name__
    if name == "PageBlockPreformatted":
        return f"```{getattr(block, 'language', '') or ''}\n{rich_text(block.text)}\n```"
    if name == "PageBlockMath":
        return f"```\n{getattr(block, 'source', '')}\n```"
    if name == "PageBlockDivider":
        return "---"
    if name == "PageBlockList":
        return "\n".join(_list_item(item, "- ") for item in getattr(block, "items", None) or [])
    if name == "PageBlockOrderedList":
        start = getattr(block, "start", None) or 1
        items = getattr(block, "items", None) or []
        return "\n".join(
            _list_item(item, f"{_ordered_number(item, start + i)}. ") for i, item in enumerate(items)
        )
    if name in ("PageBlockBlockquote", "PageBlockPullquote", "PageBlockBlockquoteBlocks"):
        blocks = getattr(block, "blocks", None)
        body = _blocks(blocks) if blocks is not None else rich_text(getattr(block, "text", None))
        caption = _caption(getattr(block, "caption", None))
        if caption:
            body = f"{body}\n— {caption}" if body else f"— {caption}"
        return _indent(body, "> ", "> ") if body else ""
    if name == "PageBlockDetails":
        return _join([rich_text(getattr(block, "title", None)), _blocks(getattr(block, "blocks", None))])
    if name == "PageBlockTable":
        return _table(block)
    if name == "PageBlockAuthorDate":
        return rich_text(getattr(block, "author", None))
    if name in _MEDIA_BLOCKS:
        caption = _caption(getattr(block, "caption", None))
        marker = f"[{_MEDIA_BLOCKS[name]}]"
        return f"{marker} {caption}" if caption else marker
    if name == "PageBlockCover":
        return _block(getattr(block, "cover", None))
    # Paragraph, headings, header, footer, kicker and whatever comes next.
    blocks = getattr(block, "blocks", None)
    if blocks is not None:
        return _blocks(blocks)
    items = getattr(block, "items", None)
    if items is not None:
        return "\n".join(_list_item(item, "- ") for item in items)
    return rich_text(getattr(block, "text", None))


def _join(parts) -> str:
    return "\n\n".join(part for part in parts if part)


def _blocks(blocks) -> str:
    return _join(_block(block) for block in blocks or [])


def rich_message_text(rich_message) -> str:
    """The whole text of a RichMessage, one block per paragraph."""
    if rich_message is None:
        return ""
    return _blocks(getattr(rich_message, "blocks", None))
