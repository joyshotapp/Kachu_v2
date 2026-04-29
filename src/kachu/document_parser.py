"""
Document / image / text parsing pipeline for Day 0 onboarding uploads.

Routing:
  image/*       → Gemini Vision  → structured text summary
  application/* or file (LINE)  → LlamaParse (PDF/DOCX) → plain text
  audio/*       → stub (ASR not yet integrated, returns "needs_manual")
  text          → direct text, no parsing needed

All parsers return a ParseResult with:
  - text: str       — the extracted human-readable text
  - source_type: str — "image_parsed" | "document_parsed" | "audio_stub" | "text"
  - confidence: float — 0.0–1.0 (1.0 = high quality parse, 0.0 = failed/stub)
  - needs_manual: bool — True when content cannot be parsed automatically
"""
from __future__ import annotations

import base64
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from .config import Settings

logger = logging.getLogger(__name__)

GEMINI_VISION_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent"
)
LLAMAPARSE_UPLOAD_URL = "https://api.cloud.llamaindex.ai/api/parsing/upload"
LLAMAPARSE_RESULT_URL = "https://api.cloud.llamaindex.ai/api/parsing/job/{job_id}/result/text"
LLAMAPARSE_POLL_INITIAL_SECONDS = 2.0
LLAMAPARSE_POLL_MAX_SECONDS = 10.0
LLAMAPARSE_POLL_TIMEOUT_SECONDS = 60.0


@dataclass
class ParseResult:
    text: str
    source_type: str
    confidence: float = 1.0
    needs_manual: bool = False
    error: str | None = None


async def parse_document(
    *,
    msg_type: str,
    content_bytes: bytes | None,
    content_text: str | None,
    mime_type: str,
    settings: "Settings",
) -> ParseResult:
    """
    Main entry point. Dispatch to the right parser based on msg_type.
    Returns a ParseResult for normal and recoverable parser/provider failures.
    Unexpected programming errors bubble so they are visible during validation.
    """
    try:
        if msg_type == "image":
            return await _parse_image(
                image_bytes=content_bytes or b"",
                mime_type=mime_type,
                api_key=settings.GOOGLE_AI_API_KEY,
            )
        elif msg_type == "file":
            return await _parse_file(
                file_bytes=content_bytes or b"",
                mime_type=mime_type,
                llamaparse_api_key=settings.LLAMAPARSE_API_KEY,
            )
        elif msg_type == "audio":
            # ASR not integrated yet — record for manual review
            return ParseResult(
                text="",
                source_type="audio_stub",
                confidence=0.0,
                needs_manual=True,
            )
        elif msg_type == "text" and content_text:
            return ParseResult(
                text=content_text,
                source_type="text",
                confidence=1.0,
            )
        else:
            return ParseResult(
                text="",
                source_type="unknown",
                confidence=0.0,
                needs_manual=True,
                error=f"Unsupported msg_type: {msg_type}",
            )
    except (
        httpx.HTTPError,
        TimeoutError,
        ValueError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        IndexError,
        TypeError,
    ) as exc:
        logger.error("Document parsing failed for msg_type=%s: %s", msg_type, exc)
        return ParseResult(
            text="",
            source_type=msg_type,
            confidence=0.0,
            needs_manual=True,
            error=str(exc),
        )


async def _parse_image(
    *,
    image_bytes: bytes,
    mime_type: str,
    api_key: str,
) -> ParseResult:
    """Use Gemini Vision to extract structured knowledge from an image."""
    if not api_key or not image_bytes:
        return ParseResult(
            text="",
            source_type="image_parsed",
            confidence=0.0,
            needs_manual=True,
            error="No GOOGLE_AI_API_KEY or empty image",
        )

    prompt = (
        "你是一位幫助微型創業者建立知識庫的 AI 助手。\n"
        "請仔細分析這張圖片並用繁體中文提取以下資訊（JSON 格式回覆）：\n"
        "- content_type: 圖片類型（menu/price_list/product/promotion/other）\n"
        "- summary: 圖片主要內容摘要（100字內）\n"
        "- items: 列表，包含圖片中所有可辨識的品項、價格、服務名稱等\n"
        "- tags: 相關關鍵字（最多8個）\n"
        "- confidence: 解析信心值（0.0-1.0）\n\n"
        "如果圖片模糊或無法辨識，confidence 填 0.2，其他欄位填空。"
    )
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime_type, "data": b64}},
                ]
            }
        ]
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{GEMINI_VISION_URL}?key={api_key}",
            json=payload,
        )
        resp.raise_for_status()

    data = resp.json()
    raw = data["candidates"][0]["content"]["parts"][0]["text"]

    # Try to extract JSON
    clean = raw.strip()
    if "```" in clean:
        parts = clean.split("```")
        # Take the block after the first ```
        for part in parts[1:]:
            candidate = part.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            try:
                parsed = json.loads(candidate)
                break
            except json.JSONDecodeError:
                continue
        else:
            parsed = {}
    else:
        try:
            parsed = json.loads(clean)
        except json.JSONDecodeError:
            parsed = {}

    summary = parsed.get("summary", raw[:500])
    items = parsed.get("items", [])
    tags = parsed.get("tags", [])
    confidence = float(parsed.get("confidence", 0.8))

    text_parts = [f"【圖片分析】{summary}"]
    if items:
        text_parts.append("品項/內容：" + "、".join(str(i) for i in items[:20]))
    if tags:
        text_parts.append("關鍵詞：" + " ".join(str(t) for t in tags))

    return ParseResult(
        text="\n".join(text_parts),
        source_type="image_parsed",
        confidence=confidence,
        needs_manual=(confidence < 0.4),
    )


async def _parse_file(
    *,
    file_bytes: bytes,
    mime_type: str,
    llamaparse_api_key: str,
) -> ParseResult:
    """Use LlamaParse to extract text from PDF/DOCX files."""
    if not llamaparse_api_key or not file_bytes:
        # Fallback: try plain text decode for simple text files
        if mime_type and "text" in mime_type:
            text = file_bytes.decode("utf-8", errors="replace")
            return ParseResult(text=text[:5000], source_type="document_parsed", confidence=0.9)
        return ParseResult(
            text="",
            source_type="document_parsed",
            confidence=0.0,
            needs_manual=True,
            error="No LLAMAPARSE_API_KEY or empty file",
        )

    # Determine file extension from mime_type
    ext_map = {
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/msword": ".doc",
        "text/plain": ".txt",
        "text/csv": ".csv",
    }
    ext = ext_map.get(mime_type, ".pdf")

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Upload file to LlamaParse
        upload_resp = await client.post(
            LLAMAPARSE_UPLOAD_URL,
            headers={"Authorization": f"Bearer {llamaparse_api_key}"},
            files={"file": (f"document{ext}", file_bytes, mime_type)},
        )
        upload_resp.raise_for_status()
        job_id = upload_resp.json().get("id")
        if not job_id:
            raise ValueError("LlamaParse did not return job_id")

        # Poll for result with a short backoff to avoid timing out on larger files.
        poll_delay = LLAMAPARSE_POLL_INITIAL_SECONDS
        deadline = time.monotonic() + LLAMAPARSE_POLL_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            await asyncio.sleep(poll_delay)
            result_resp = await client.get(
                LLAMAPARSE_RESULT_URL.format(job_id=job_id),
                headers={"Authorization": f"Bearer {llamaparse_api_key}"},
            )
            if result_resp.status_code == 200:
                text = result_resp.json().get("text", "")
                return ParseResult(
                    text=text[:5000],  # cap at 5000 chars for knowledge entry
                    source_type="document_parsed",
                    confidence=0.9 if text else 0.3,
                    needs_manual=not bool(text),
                )
            if result_resp.status_code not in {202, 404}:
                result_resp.raise_for_status()
            poll_delay = min(poll_delay * 1.5, LLAMAPARSE_POLL_MAX_SECONDS)

    return ParseResult(
        text="",
        source_type="document_parsed",
        confidence=0.0,
        needs_manual=True,
        error="LlamaParse timed out before result was ready",
    )
