"""Turn an ExtractResult into the MCP tool result shared by every read tool."""

import base64
from typing import List, Optional

from fastmcp.tools.tool import ToolResult
from mcp.types import ImageContent, TextContent

from cn_extras.extract import ExtractResult
from cn_extras.output import clean_label, format_header, format_text_result


def error_result(message: str) -> ToolResult:
    return ToolResult(content=[TextContent(type="text", text=f"Error: {message}")])


def render_result(
    result: ExtractResult,
    *,
    filename: str,
    source: str,
    offset: int = 0,
    max_chars: Optional[int] = None,
    notes: Optional[List[str]] = None,
) -> ToolResult:
    """Render text (paginated, wrapped as untrusted), an image, or a refusal.

    ``source`` names where the content came from (sender, file owner) for the
    untrusted marker; ``notes`` are tool-level notes shown before the result's.
    """
    all_notes = list(notes or []) + result.notes
    if result.kind == "image":
        header = format_header(
            filename=filename,
            mime_type=result.mime_type,
            engine=result.engine,
            notes=all_notes
            + [f"untrusted image from: {clean_label(source) or 'unknown'}"],
        )
        return ToolResult(
            content=[
                TextContent(type="text", text=header),
                ImageContent(
                    type="image",
                    data=base64.b64encode(result.image_data).decode("ascii"),
                    mimeType=result.image_mime_type,
                ),
            ]
        )

    if result.kind == "unsupported":
        header = format_header(
            filename=filename,
            mime_type=result.mime_type,
            engine=result.engine,
            notes=all_notes,
        )
        reason = clean_label(result.reason or "", limit=500)
        return ToolResult(
            content=[
                TextContent(type="text", text=f"{header}\n\nNot readable: {reason}")
            ]
        )

    if result.page_count:
        all_notes.append(f"{result.page_count} page(s)")
    try:
        text = format_text_result(
            text=result.text or "",
            filename=filename,
            mime_type=result.mime_type,
            engine=result.engine,
            source=source,
            offset=offset,
            max_chars=max_chars,
            notes=all_notes,
        )
    except ValueError as exc:
        return error_result(str(exc))
    return ToolResult(content=[TextContent(type="text", text=text)])
