"""Prepare images for return as MCP ImageContent.

Claude reads images natively, so images go back as images, resized so the long
edge is at most 1568 px (larger buys no extra detail and costs context).
Pillow is an optional dependency (extra ``cn``); without it, images that are
already in a supported format and small enough pass through unchanged.
"""

import io
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

MAX_LONG_EDGE_PX = 1568
# Anthropic's per-image limit is 5 MB of base64; stay well under it.
MAX_IMAGE_BYTES = 3_500_000
# Refuse to decode beyond this: 40 MP RGBA is ~160 MB in memory.
MAX_PIXELS = 40_000_000

# Formats MCP clients (and Claude) accept directly.
NATIVE_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
# Formats we can only return after converting with Pillow.
CONVERTIBLE_IMAGE_MIME_TYPES = {"image/bmp", "image/tiff", "image/x-ms-bmp"}

_PIL_FORMAT = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/webp": "WEBP",
}
_MIME_BY_PIL_FORMAT = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
    "BMP": "image/bmp",
    "TIFF": "image/tiff",
}


@dataclass
class PreparedImage:
    data: bytes
    mime_type: str
    note: Optional[str] = None


class ImageNotUsableError(ValueError):
    """The image cannot be returned (unsupported format, too large, damaged)."""


def _pillow():
    try:
        from PIL import Image

        return Image
    except ImportError:
        return None


def prepare_image(data: bytes, mime_type: str) -> PreparedImage:
    Image = _pillow()
    if Image is None:
        if mime_type not in NATIVE_IMAGE_MIME_TYPES:
            raise ImageNotUsableError(
                f"{mime_type} needs conversion, and Pillow is not installed."
            )
        if len(data) > MAX_IMAGE_BYTES:
            raise ImageNotUsableError(
                "image is too large to return and Pillow is not installed to resize it."
            )
        return PreparedImage(
            data, mime_type, "returned unresized (Pillow not installed)"
        )

    declared = mime_type
    try:
        with Image.open(io.BytesIO(data)) as img:
            # Header-only so far: reject pixel bombs before decoding. Pillow only
            # raises above 2x MAX_IMAGE_PIXELS and merely warns below that.
            width, height = img.size
            if width * height > MAX_PIXELS:
                raise ImageNotUsableError(
                    f"the image is too large ({width}x{height} pixels)."
                )
            # Mail clients mislabel images; the media type we return must match
            # the bytes or the model API rejects the image. Any decoded format
            # outside the directly returnable ones (ICO, PPM, MPO, TGA...) is
            # re-encoded rather than passed through under the declared type.
            detected = _MIME_BY_PIL_FORMAT.get(img.format or "")
            mime_type = (
                detected or "image/x-pillow-" + (img.format or "unknown").lower()
            )
            resized = max(width, height) > MAX_LONG_EDGE_PX
            needs_convert = mime_type not in _PIL_FORMAT
            if not resized and not needs_convert and len(data) <= MAX_IMAGE_BYTES:
                note = (
                    f"detected as {mime_type} (declared {declared})"
                    if mime_type != declared
                    else None
                )
                return PreparedImage(data, mime_type, note)

            if resized and img.format == "JPEG":
                # Let the JPEG decoder downscale while decoding (much less memory).
                img.draft("RGB", (MAX_LONG_EDGE_PX, MAX_LONG_EDGE_PX))
            img.load()
            if resized:
                img.thumbnail((MAX_LONG_EDGE_PX, MAX_LONG_EDGE_PX))

            out_mime = mime_type if not needs_convert else "image/png"
            encoded = _encode(img, out_mime)
            if len(encoded) > MAX_IMAGE_BYTES:
                out_mime = "image/jpeg"
                encoded = _encode(img, out_mime)
    except ImageNotUsableError:
        raise
    except Exception as exc:  # Pillow raises a wide range of errors on bad input
        # DecompressionBombError lands here too, which is what we want.
        logger.warning(
            "Could not process image (%s): %s", mime_type, type(exc).__name__
        )
        raise ImageNotUsableError("the image could not be decoded") from exc

    if len(encoded) > MAX_IMAGE_BYTES:
        raise ImageNotUsableError("the image is still too large after resizing.")

    notes = []
    if mime_type != declared:
        notes.append(f"detected as {mime_type} (declared {declared})")
    if resized:
        notes.append(f"resized from {width}x{height} to fit {MAX_LONG_EDGE_PX}px")
    if out_mime != mime_type:
        notes.append(f"converted from {mime_type} to {out_mime}")
    return PreparedImage(encoded, out_mime, "; ".join(notes) or None)


def _encode(img, mime_type: str) -> bytes:
    fmt = _PIL_FORMAT[mime_type]
    if fmt == "JPEG" and img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if fmt == "PNG" and img.mode not in ("RGB", "RGBA", "L", "LA", "P"):
        img = img.convert("RGBA")
    buf = io.BytesIO()
    kwargs = {"quality": 85} if fmt in ("JPEG", "WEBP") else {"optimize": True}
    img.save(buf, format=fmt, **kwargs)
    return buf.getvalue()
