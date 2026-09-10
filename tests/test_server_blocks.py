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

    def test_image_block_declares_the_mime_the_decision_was_made_on(self, tmp_path):
        # The path's extension is one the SDK's own extension-to-mime table does
        # not recognise (Telethon can derive such an extension from the mime via
        # the host's mime database), so if the mime were guessed from the
        # extension it would come back as application/octet-stream. Declaring
        # the subtype explicitly must win regardless.
        # `to_image_content()` reads the file, so it needs to actually exist.
        path = tmp_path / "team-1.jfif"
        path.write_bytes(b"fake-jpeg-bytes")
        blocks = _media_blocks(result(mime="image/jpeg", path=str(path)))
        assert blocks[1].to_image_content().mime_type == "image/jpeg"

    def test_document_comes_back_as_metadata_only(self):
        blocks = _media_blocks(result(mime="application/pdf", path="/tmp/team-1.pdf"))
        assert len(blocks) == 1
        assert blocks[0]["inlined"] is False
        assert "not an image" in blocks[0]["inline_note"]

    def test_oversized_picture_comes_back_as_metadata_only(self):
        blocks = _media_blocks(result(size=9_000_000))
        assert len(blocks) == 1
        assert "inline limit" in blocks[0]["inline_note"]

    def test_does_not_mutate_the_caller_s_dict(self):
        original = result()
        _media_blocks(original)
        assert "inlined" not in original
        assert "inline_note" not in original
