"""Phase I, products: Hugging Face Spaces, the largest public directory of
live AI products (hosted apps/demos with real product pages).

The hub API is queried several ways (top-by-likes plus topical searches)
and merged, so a single ranking's blind spots don't cap the corpus. Each
Space is a runnable product with a public page — every record traces to
a live URL. Spaces are free to run, hence pricingModel=FREE; a paid
marketplace connector (Product Hunt with a token, AppSumo) would slot
into this same interface and populate the other enum values.
"""
from __future__ import annotations

import asyncio
import logging

from ..http_client import HttpClient, HttpStatusError
from ..resolver import EntityResolver
from . import record

log = logging.getLogger("pipeline.products")

SOURCE_NAME = "Hugging Face Spaces"
API = "https://huggingface.co/api/spaces"
SEARCHES = [None, "llm", "chatbot", "agent", "diffusion", "image generation", "text to speech", "rag", "video", "ocr"]


async def fetch_products(http: HttpClient, resolver: EntityResolver, target: int = 1100) -> list[dict]:
    async def _query(query):
        url = f"{API}?limit=1000&sort=likes&direction=-1"
        if query:
            url += f"&search={query.replace(' ', '%20')}"
        try:
            spaces = await http.get(url, expect="json")
            return spaces if isinstance(spaces, list) else []
        except HttpStatusError as err:
            log.warning("spaces query %r failed: %s", query, err)
            return []

    by_id: dict[str, dict] = {}
    for spaces in await asyncio.gather(*(_query(q) for q in SEARCHES)):
        for space in spaces:
            if isinstance(space, dict) and space.get("id") and not space.get("private"):
                by_id[space["id"]] = space
    ranked = sorted(by_id.values(), key=lambda s: s.get("likes", 0), reverse=True)
    log.info("products: %d unique spaces merged from %d queries", len(ranked), len(SEARCHES))

    records = []
    for space in ranked[:target]:
        space_id = space["id"]
        author, _, name_part = space_id.partition("/")
        title = ((space.get("cardData") or {}).get("title") or name_part).strip()
        records.append(
            record(
                "PRODUCT",
                SOURCE_NAME,
                f"https://huggingface.co/spaces/{space_id}",
                {
                    "startupName": resolver.resolve(author, "products"),
                    "pricingModel": "FREE",
                    "data": {"productName": title, "likes": space.get("likes", 0)},
                },
            )
        )
    log.info("products: %d records", len(records))
    return records
