from __future__ import annotations

import base64
import logging
import os
from functools import lru_cache
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GEMINI_VISION_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent"
)


@lru_cache(maxsize=4)
def _build_langfuse_client(
    public_key: str,
    secret_key: str,
    host: str,
) -> Any | None:
    try:
        from langfuse import Langfuse
        return Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
    except ImportError:
        return None


def _get_langfuse_client() -> Any | None:
    """Return a cached Langfuse client if credentials are configured, else None."""
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")
    if not public_key or not secret_key:
        return None
    host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
    return _build_langfuse_client(public_key, secret_key, host)


async def analyze_image_url(
    *,
    image_url: str,
    prompt: str,
    api_key: str,
) -> str:
    """
    Call Gemini Vision via URL reference (for publicly accessible images).
    Returns the text response.
    """
    payload: dict[str, Any] = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"image_url": {"url": image_url}},
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
    return data["candidates"][0]["content"]["parts"][0]["text"]


async def analyze_image_bytes(
    *,
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    prompt: str,
    api_key: str,
) -> str:
    """
    Call Gemini Vision with raw image bytes (base64 encoded).
    Returns the text response.
    """
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    payload: dict[str, Any] = {
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
    return data["candidates"][0]["content"]["parts"][0]["text"]


async def generate_text(
    *,
    prompt: str,
    system: str = "",
    model: str = "gemini/gemini-2.0-flash",
    api_key: str = "",
    openai_api_key: str = "",
    run_id: str | None = None,
    generation_name: str | None = None,
) -> str:
    """
    Generate text via LiteLLM (supports gemini/, gpt-4o, claude, etc.).
    When run_id is provided and LANGFUSE_* env vars are set, the generation
    is recorded as a Langfuse observation linked to the trace.
    """
    import litellm

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    completion_kwargs: dict[str, Any] = {"model": model, "messages": messages}
    selected_api_key = ""
    if model.startswith("gemini/"):
        selected_api_key = api_key
    elif model.startswith("gpt") or model.startswith("openai/"):
        selected_api_key = openai_api_key
    else:
        selected_api_key = api_key or openai_api_key
    if selected_api_key:
        completion_kwargs["api_key"] = selected_api_key

    response = await litellm.acompletion(**completion_kwargs)
    result: str = response.choices[0].message.content or ""

    # Optional Langfuse generation tracking
    if run_id:
        lf = _get_langfuse_client()
        if lf is not None:
            try:
                trace = lf.trace(id=run_id)
                trace.generation(
                    name=generation_name or "generate_text",
                    model=model,
                    input=messages,
                    output=result,
                    metadata={"run_id": run_id},
                )
                lf.flush()
            except Exception:  # noqa: BLE001
                logger.debug("Langfuse generation tracking failed silently")

    return result

