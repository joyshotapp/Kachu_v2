from __future__ import annotations

import json
import logging

import httpx

logger = logging.getLogger(__name__)

GEMINI_EMBEDDING_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-embedding-2:embedContent"
)
GEMINI_EMBEDDING_DIM = 1536


async def get_embedding(text: str, api_key: str, *, is_query: bool = False) -> list[float]:
    """Return Gemini Embedding 2 vector for *text* (dim 1536).

    When is_query=True, formats text as a search query (asymmetric retrieval).
    When is_query=False, formats text as a document to be indexed.
    Returns an empty list on failure; callers fall back to keyword-based retrieval.
    """
    if not api_key or not text.strip():
        return []
    if is_query:
        formatted = f"task: search result | query: {text[:7900]}"
    else:
        formatted = f"title: none | text: {text[:7900]}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{GEMINI_EMBEDDING_URL}?key={api_key}",
                json={
                    "content": {"parts": [{"text": formatted}]},
                    "outputDimensionality": GEMINI_EMBEDDING_DIM,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["embedding"]["values"]
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("Gemini embedding failed: %s", exc)
        return []
