"""Phase II, news: five AI news sources, full text, 24-hour freshness.

Sources (RSS): TechCrunch AI, OpenAI News, Hugging Face Blog, The Verge
AI, MIT Technology Review AI. VentureBeat's feed answered 429 during
development — exactly the class of failure the HTTP layer is built to
absorb — and was swapped for MIT Technology Review; the connector list
is data, not code, so swapping a source is a one-line change.

Freshness: feed pubDate inside the window is a proof. Where a feed
omits dates, the first-seen heuristic applies (architecture.pdf): the
URL must be absent from the persistent seen-store AND sit in the
feed's most recent items; the record is then marked
freshnessBasis='first-seen' so downstream consumers can weigh it
differently from a proven date. Articles are fetched for full text;
the LLM engine (or the deterministic mention scan without keys)
extracts the canonical organisations mentioned.
"""
from __future__ import annotations

import json
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from ..dates import is_fresh, iso_z, normalize_datetime
from ..http_client import HttpClient, HttpStatusError
from ..llm_engine import LlmEngine
from ..resolver import EntityResolver
from ..text_extract import extract_full_text
from . import record

log = logging.getLogger("pipeline.news")

FEEDS = [
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("OpenAI News", "https://openai.com/news/rss.xml"),
    ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml"),
    ("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ("MIT Technology Review AI", "https://www.technologyreview.com/topic/artificial-intelligence/feed/"),
]
ATOM = {"a": "http://www.w3.org/2005/Atom"}
STATE_PATH = os.path.join("output", "state", "seen_news.json")
EXTRACT_INSTRUCTION = (
    "Extract from this news article: {\"headline\": string, \"organizations\": [string]}. "
    "organizations are companies/labs named in the text."
)


async def fetch_news(
    http: HttpClient, resolver: EntityResolver, engine: LlmEngine, freshness_hours: int = 24
) -> list[dict]:
    now = datetime.now(timezone.utc)
    seen = _load_state()
    records, new_seen = [], {}
    for source_name, feed_url in FEEDS:
        try:
            xml = await http.get(feed_url)
            items = _parse_feed(xml)
        except Exception as err:
            log.warning("%s feed failed: %s", source_name, err)
            continue
        log.info("%s: %d feed items", source_name, len(items))
        for position, item in enumerate(items):
            url = item["link"]
            if not url:
                continue
            published = normalize_datetime(item.get("date"), now)
            basis = "published-date"
            if published is not None and not is_fresh(published, now, freshness_hours):
                new_seen[url] = iso_z(now)
                continue
            if published is None:
                if url in seen or position >= 12:
                    continue
                basis = "first-seen"
                published = now
            new_seen[url] = iso_z(now)
            try:
                extracted = extract_full_text(await http.get(url))
                text = extracted["text"]
                if not published and basis == "published-date":
                    published = normalize_datetime(extracted.get("published_raw"), now) or published
            except HttpStatusError:
                text = item.get("summary", "")
            if not text:
                text = item.get("summary", "")

            organisations, tier = await _organisations(engine, resolver, item["title"], text)
            records.append(
                record(
                    "NEWS",
                    source_name,
                    url,
                    {
                        "title": item["title"],
                        "publishedAt": iso_z(published),
                        "freshnessBasis": basis,
                        "summary": (item.get("summary") or text[:280])[:600],
                        "fullText": text[:12000],
                        "entities": organisations,
                        "extractionTier": tier,
                    },
                )
            )
    _save_state({**seen, **new_seen})
    records.sort(key=lambda r: r["content"]["publishedAt"], reverse=True)
    log.info("news: %d fresh articles across %d sources", len(records), len(FEEDS))
    return records


async def _organisations(engine: LlmEngine, resolver: EntityResolver, title: str, text: str):
    if engine.available_tiers():
        result = await engine.extract_json(
            EXTRACT_INSTRUCTION, f"{title}\n\n{text}", ("headline", "organizations"),
            keywords=tuple(n.lower() for n in resolver.canonicals[:20]),
        )
        if result is not None:
            obj, tier = result
            names = [resolver.resolve(str(o), "news") for o in obj.get("organizations", [])]
            return sorted(set(n for n in names if n)), tier
    return resolver.find_mentions(f"{title}\n{text[:6000]}"), "deterministic"


def _parse_feed(xml: str) -> list[dict]:
    root = ET.fromstring(xml)
    items = []
    if root.tag.endswith("feed"):  # Atom
        for entry in root.findall("a:entry", ATOM):
            link = ""
            for node in entry.findall("a:link", ATOM):
                if node.get("rel") in (None, "alternate"):
                    link = node.get("href", "")
                    break
            items.append({
                "title": (entry.findtext("a:title", default="", namespaces=ATOM) or "").strip(),
                "link": link,
                "date": entry.findtext("a:published", default="", namespaces=ATOM)
                or entry.findtext("a:updated", default="", namespaces=ATOM),
                "summary": (entry.findtext("a:summary", default="", namespaces=ATOM) or "").strip(),
            })
        return items
    for node in root.iter("item"):  # RSS 2.0
        items.append({
            "title": (node.findtext("title") or "").strip(),
            "link": (node.findtext("link") or "").strip(),
            "date": node.findtext("pubDate") or node.findtext("date"),
            "summary": (node.findtext("description") or "").strip(),
        })
    return items


def _load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
