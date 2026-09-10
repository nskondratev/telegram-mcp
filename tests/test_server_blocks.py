"""Assembling the answer of get_message_media: metadata always, the picture when it fits."""
from mcp.server.mcpserver import Image

from telegram_mcp.server import _media_blocks


def result(**overrides):
    base = {
        "path": "/tmp/team-1.png",
        "mime": "image/png",
        "size": 1024,
        "media_type": "Photo",
    }
    base.update(overrides)
    return base


class TestMediaBlocks:
    def test_picture_rides_along_with_the_metadata(self):
        blocks = _media_blocks(result())
        assert len(blocks) == 2
        assert blocks[0]["inlined"] is True
        assert blocks[0]["inline_note"] is None
        assert isinstance(blocks[1], Image)
        assert str(blocks[1].path) == "/tmp/team-1.png"

    def test_document_comes_back_as_metadata_only(self):
        blocks = _media_blocks(result(mime="application/pdf", path="/tmp/team-1.pdf"))
        assert len(blocks) == 1
        assert blocks[0]["inlined"] is False
        assert "not an image" in blocks[0]["inline_note"]

    def test_oversized_picture_comes_back_as_metadata_only(self):
        blocks = _media_blocks(result(size=9_000_000))
        assert len(blocks) == 1
        assert "inline limit" in blocks[0]["inline_note"]
