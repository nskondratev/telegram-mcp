"""Tests for rich messages: text that lives in blocks, not in Message.message.

A message written with rich formatting (layer 229) arrives with an empty
``message`` and the whole text in ``rich_message`` — the same block types
Instant View pages are made of. The fixtures below are real Telethon types, so a
renamed constructor or field shows up here and not in production.
"""
from telethon.tl.types import (
    PageBlockBlockquote,
    PageBlockDetails,
    PageBlockDivider,
    PageBlockList,
    PageBlockOrderedList,
    PageBlockParagraph,
    PageBlockPhoto,
    PageBlockPreformatted,
    PageBlockTable,
    PageBlockUnsupported,
    PageCaption,
    PageListItemBlocks,
    PageListItemText,
    PageListOrderedItemText,
    PageTableCell,
    PageTableRow,
    RichMessage,
    TextBold,
    TextConcat,
    TextCustomEmoji,
    TextEmpty,
    TextFixed,
    TextItalic,
    TextPlain,
    TextStrike,
    TextUnderline,
    TextUrl,
)

from telegram_mcp.rich import rich_message_text


def rich(*blocks):
    return RichMessage(blocks=list(blocks), photos=[], documents=[])


def para(*texts):
    if len(texts) == 1:
        return PageBlockParagraph(text=texts[0])
    return PageBlockParagraph(text=TextConcat(texts=list(texts)))


def plain(text):
    return TextPlain(text=text)


class TestRichText:
    def test_none_renders_nothing(self):
        assert rich_message_text(None) == ""

    def test_paragraphs_are_separated_by_a_blank_line(self):
        message = rich(para(plain("First.")), para(plain("Second.")))
        assert rich_message_text(message) == "First.\n\nSecond."

    def test_inline_formatting_follows_telethon_markdown(self):
        message = rich(
            para(
                TextBold(text=plain("bold")),
                plain(" "),
                TextItalic(text=plain("italic")),
                plain(" "),
                TextStrike(text=plain("gone")),
                plain(" "),
                TextFixed(text=plain("code")),
                plain(" "),
                TextUrl(text=plain("link"), url="https://example.com", webpage_id=0),
                plain(" "),
                TextUnderline(text=plain("under")),
            )
        )
        assert rich_message_text(message) == (
            "**bold** __italic__ ~~gone~~ `code` [link](https://example.com) under"
        )

    def test_nested_formatting(self):
        message = rich(para(TextBold(text=TextConcat(texts=[plain("a "), TextFixed(text=plain("b"))]))))
        assert rich_message_text(message) == "**a `b`**"

    def test_empty_text_and_custom_emoji(self):
        message = rich(para(TextEmpty(), TextCustomEmoji(document_id=1, alt="🔥"), plain(" ok")))
        assert rich_message_text(message) == "🔥 ok"

    def test_preformatted_block_keeps_its_language(self):
        message = rich(PageBlockPreformatted(text=plain("prefix:<id>"), language="text"))
        assert rich_message_text(message) == "```text\nprefix:<id>\n```"

    def test_bullet_list_with_checkboxes(self):
        message = rich(
            PageBlockList(
                items=[
                    PageListItemText(text=plain("one")),
                    PageListItemText(text=plain("done"), checkbox=True, checked=True),
                    PageListItemText(text=plain("todo"), checkbox=True, checked=False),
                ]
            )
        )
        assert rich_message_text(message) == "- one\n- [x] done\n- [ ] todo"

    def test_list_item_made_of_blocks_is_indented(self):
        message = rich(
            PageBlockList(items=[PageListItemBlocks(blocks=[para(plain("line 1")), para(plain("line 2"))])])
        )
        assert rich_message_text(message) == "- line 1\n\n  line 2"

    def test_ordered_list_uses_the_numbers_it_carries(self):
        message = rich(
            PageBlockOrderedList(
                items=[
                    PageListOrderedItemText(text=plain("first"), num="1"),
                    PageListOrderedItemText(text=plain("second"), value=2),
                    PageListOrderedItemText(text=plain("third")),
                ]
            )
        )
        assert rich_message_text(message) == "1. first\n2. second\n3. third"

    def test_blockquote_is_quoted_line_by_line(self):
        message = rich(PageBlockBlockquote(text=plain("one\ntwo"), caption=plain("author")))
        assert rich_message_text(message) == "> one\n> two\n> — author"

    def test_details_show_title_and_body(self):
        message = rich(PageBlockDetails(title=plain("More"), blocks=[para(plain("hidden"))]))
        assert rich_message_text(message) == "More\n\nhidden"

    def test_table_renders_rows(self):
        cell = lambda text: PageTableCell(text=plain(text))  # noqa: E731
        message = rich(
            PageBlockTable(
                title=plain("Prices"),
                rows=[
                    PageTableRow(cells=[cell("a"), cell("b")]),
                    PageTableRow(cells=[cell("1"), PageTableCell()]),
                ],
            )
        )
        assert rich_message_text(message) == "Prices\n| a | b |\n| 1 |  |"

    def test_media_block_shows_a_marker_and_its_caption(self):
        message = rich(
            PageBlockPhoto(photo_id=1, caption=PageCaption(text=plain("diagram"), credit=TextEmpty()))
        )
        assert rich_message_text(message) == "[photo] diagram"

    def test_divider_and_unsupported_blocks(self):
        message = rich(para(plain("a")), PageBlockDivider(), PageBlockUnsupported(), para(plain("b")))
        assert rich_message_text(message) == "a\n\n---\n\nb"

