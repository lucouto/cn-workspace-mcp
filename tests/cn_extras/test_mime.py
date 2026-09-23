import pytest

from cn_extras.mime import (
    AttachmentLookupError,
    decode_base64url,
    find_attachment,
    get_header,
    walk_attachments,
)
from tests.cn_extras import fixtures as fx


def _by_id(parts):
    return {p.part_id: p for p in parts}


class TestWalk:
    def test_finds_nested_forwarded_and_inline_parts_but_not_bodies(self):
        parts = _by_id(walk_attachments(fx.complex_message()["payload"]))
        assert set(parts) == {"1", "2", "3", "4.0.1"}

    def test_inline_data_part_has_no_attachment_id(self):
        csv = _by_id(walk_attachments(fx.complex_message()["payload"]))["2"]
        assert csv.attachment_id is None
        assert decode_base64url(csv.inline_data) == "nom;pays\nJoão;Brésil\n".encode()

    def test_part_inside_forwarded_message_is_flagged(self):
        parts = _by_id(walk_attachments(fx.complex_message()["payload"]))
        assert parts["4.0.1"].in_attached_message is True
        assert parts["1"].in_attached_message is False

    def test_small_inline_image_with_content_id_is_signature(self):
        parts = _by_id(walk_attachments(fx.complex_message()["payload"]))
        assert parts["3"].likely_signature_image is True
        assert parts["1"].likely_signature_image is False

    def test_large_inline_image_is_not_signature(self):
        msg = fx.gmail_message(
            "m",
            [
                fx.gmail_part(
                    "1",
                    "image/jpeg",
                    "scan.jpg",
                    attachment_id="A",
                    size=900_000,
                    disposition="inline",
                )
            ],
        )
        assert walk_attachments(msg["payload"])[0].likely_signature_image is False

    def test_attachment_disposition_without_filename_gets_a_name(self):
        msg = fx.gmail_message(
            "m",
            [
                fx.gmail_part(
                    "7",
                    "application/pdf",
                    "",
                    attachment_id="A",
                    size=10,
                    disposition="attachment",
                )
            ],
        )
        (part,) = walk_attachments(msg["payload"])
        assert part.filename == "part-7"

    def test_depth_first_mime_order(self):
        ids = [p.part_id for p in walk_attachments(fx.complex_message()["payload"])]
        assert ids == ["1", "2", "3", "4.0.1"]

    def test_empty_payload(self):
        assert walk_attachments({}) == []

    def test_header_lookup_is_case_insensitive(self):
        payload = fx.complex_message()["payload"]
        assert get_header(payload, "from") == "Marie <marie@example.org>"
        assert get_header(payload, "X-Missing") == ""


class TestFind:
    def setup_method(self):
        self.parts = walk_attachments(fx.complex_message()["payload"])

    def test_part_id_wins(self):
        assert (
            find_attachment(self.parts, part_id="2", attachment_id="ATT-PDF").part_id
            == "2"
        )

    def test_unknown_part_id_lists_available(self):
        with pytest.raises(AttachmentLookupError, match="1, 2, 3, 4.0.1"):
            find_attachment(self.parts, part_id="99")

    def test_attachment_id_match(self):
        assert find_attachment(self.parts, attachment_id="ATT-PDF").part_id == "1"

    def test_rotated_attachment_id_falls_back_to_filename(self):
        assert (
            find_attachment(
                self.parts, attachment_id="STALE", filename="RAPPORT.pdf"
            ).part_id
            == "1"
        )

    def test_rotated_attachment_id_with_single_attachment(self):
        (only,) = [p for p in self.parts if p.part_id == "1"]
        assert find_attachment([only], attachment_id="STALE") is only

    def test_rotated_attachment_id_with_several_is_ambiguous(self):
        with pytest.raises(AttachmentLookupError, match="rotates"):
            find_attachment(self.parts, attachment_id="STALE")

    def test_duplicate_filenames_are_ambiguous(self):
        dup = fx.gmail_message(
            "m",
            [
                fx.gmail_part(
                    "1", "application/pdf", "a.pdf", attachment_id="A", size=1
                ),
                fx.gmail_part(
                    "2", "application/pdf", "a.pdf", attachment_id="B", size=1
                ),
            ],
        )
        with pytest.raises(AttachmentLookupError, match="Several"):
            find_attachment(walk_attachments(dup["payload"]), filename="a.pdf")

    def test_no_attachments(self):
        with pytest.raises(AttachmentLookupError, match="no attachments"):
            find_attachment([])


def test_decode_base64url_handles_missing_padding_and_rejects_garbage():
    assert decode_base64url(fx.b64url(b"ab")) == b"ab"
    with pytest.raises(ValueError):
        decode_base64url("a")
