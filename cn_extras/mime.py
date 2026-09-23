"""Walk a Gmail message payload and find its attachments.

Upstream's ``gmail.gmail_tools._extract_attachments`` only lists parts that have
both a filename and an ``attachmentId``. Gmail inlines small parts in
``body.data`` with no ``attachmentId``, and callers need the MIME ``partId``
(stable across ``messages.get`` calls, unlike attachment IDs, which Gmail
rotates) plus the Content-Disposition to spot signature images. Hence this
separate walker; it is pure and has no Google API dependency.
"""

import base64
import binascii
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# Inline images under this size with an inline disposition or a Content-ID are
# almost always logos and signature images, not documents the user cares about.
SIGNATURE_IMAGE_MAX_BYTES = 20 * 1024

# Guard against pathological MIME trees (mail bombs, generated digests).
MAX_PARTS_WALKED = 1000


@dataclass
class AttachmentPart:
    part_id: str
    filename: str
    mime_type: str
    size_bytes: int
    attachment_id: Optional[str]
    inline_data: Optional[str]
    disposition: Optional[str]
    content_id: Optional[str]
    in_attached_message: bool

    @property
    def likely_signature_image(self) -> bool:
        return (
            self.mime_type.startswith("image/")
            and (self.disposition == "inline" or bool(self.content_id))
            and self.size_bytes < SIGNATURE_IMAGE_MAX_BYTES
        )


def _headers(part: Dict[str, Any]) -> Dict[str, str]:
    return {
        (h.get("name") or "").lower(): h.get("value") or ""
        for h in part.get("headers") or []
    }


def _disposition(headers: Dict[str, str]) -> Optional[str]:
    raw = headers.get("content-disposition", "")
    if not raw:
        return None
    return raw.split(";", 1)[0].strip().lower() or None


def get_header(payload: Dict[str, Any], name: str) -> str:
    """Return a top-level message header value, or "" when absent."""
    return _headers(payload).get(name.lower(), "")


ROOT_PART_ID = "root"


def walk_attachments(
    payload: Dict[str, Any], stats: Optional[Dict[str, Any]] = None
) -> List[AttachmentPart]:
    """Return every attachment-like part of ``payload``, depth-first in MIME order.

    A part counts as an attachment when it carries data (``attachmentId`` or
    inline ``body.data``) and either has a filename or is explicitly marked
    ``Content-Disposition: attachment``. Message bodies (text/plain, text/html
    without a filename) and multipart containers are skipped.
    """
    found: List[AttachmentPart] = []
    pending = [(payload, False)]
    walked = 0
    while pending and walked < MAX_PARTS_WALKED:
        part, in_attached = pending.pop()
        walked += 1

        mime_type = (part.get("mimeType") or "application/octet-stream").lower()
        body = part.get("body") or {}
        headers = _headers(part)
        disposition = _disposition(headers)
        filename = part.get("filename") or ""
        attachment_id = body.get("attachmentId")
        inline_data = body.get("data")
        has_data = bool(attachment_id or inline_data)
        is_container = mime_type.startswith("multipart/")

        if has_data and not is_container and (filename or disposition == "attachment"):
            part_id = part.get("partId") or ROOT_PART_ID
            found.append(
                AttachmentPart(
                    part_id=part_id,
                    filename=filename or f"part-{part_id}",
                    mime_type=mime_type,
                    size_bytes=int(body.get("size") or 0),
                    attachment_id=attachment_id,
                    inline_data=inline_data if not attachment_id else None,
                    disposition=disposition,
                    content_id=headers.get("content-id") or None,
                    in_attached_message=in_attached,
                )
            )

        children = part.get("parts") or []
        if children:
            nested = in_attached or mime_type == "message/rfc822"
            pending.extend((child, nested) for child in reversed(children))
    if stats is not None:
        stats["truncated"] = bool(pending)
    return found


class AttachmentLookupError(ValueError):
    """The requested attachment could not be resolved unambiguously."""


def find_attachment(
    parts: List[AttachmentPart],
    *,
    part_id: Optional[str] = None,
    attachment_id: Optional[str] = None,
    filename: Optional[str] = None,
) -> AttachmentPart:
    """Pick one attachment. ``part_id`` wins, then ``attachment_id``, then filename.

    With no selector and exactly one attachment, that one is returned.
    """
    if not parts:
        raise AttachmentLookupError("This message has no attachments.")

    if part_id:
        for p in parts:
            if p.part_id == part_id:
                return p
        raise AttachmentLookupError(
            f"No attachment with part_id '{part_id}'. Available part_ids: "
            + ", ".join(p.part_id for p in parts)
        )

    if attachment_id:
        for p in parts:
            if p.attachment_id == attachment_id:
                return p
        # Gmail rotates attachment IDs between fetches; fall through to the
        # filename / single-attachment rules rather than failing outright.

    if filename:
        wanted = filename.strip().lower()
        matches = [p for p in parts if p.filename.lower() == wanted]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AttachmentLookupError(
                f"Several attachments are named '{filename}'; pass part_id "
                "instead: " + ", ".join(p.part_id for p in matches)
            )
        raise AttachmentLookupError(
            f"No attachment named '{filename}'. Call gmail_list_attachments to "
            "see the available files."
        )

    if len(parts) == 1:
        return parts[0]
    raise AttachmentLookupError(
        "This message has several attachments and the attachment_id did not "
        "match (Gmail rotates them). Pass part_id from gmail_list_attachments."
        if attachment_id
        else "This message has several attachments; pass part_id from "
        "gmail_list_attachments."
    )


def decode_base64url(data: str) -> bytes:
    """Decode Gmail's unpadded base64url payloads."""
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("attachment data is not valid base64url") from exc
