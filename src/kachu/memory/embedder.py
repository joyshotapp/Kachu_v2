from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"


async def get_embedding(text: str, api_key: str) -> list[float]:
    """Return OpenAI embedding vector for *text* using text-embedding-3-small (dim 1536).

    Returns an empty list when *api_key* is absent or any network/API error occurs.
    The caller should treat an empty return as "no embedding available" and fall back
    to keyword-based retrieval.
    """
    if not api_key or not text.strip():
        return []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                OPENAI_EMBEDDINGS_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"input": text[:8000], "model": OPENAI_EMBEDDING_MODEL},
            )
            resp.raise_for_status()
            data = resp.json()
            return data["data"][0]["embedding"]
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("OpenAI embedding failed: %s", exc)
        return []
