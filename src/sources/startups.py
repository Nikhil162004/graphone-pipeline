"""Phase I, startups: the Y Combinator company directory.

One bulk JSON document (~5k companies) published from the public
directory — a single request replaces thousands of page scrapes, which
is the first scalability lesson of the pipeline: always prefer the
bulk/structured route a source already offers. The same connector
pattern (fetch -> normalise -> resolve -> record) is what a
Crunchbase/Wellfound HTML connector would implement behind this
interface when no bulk route exists.
"""
from __future__ import annotations

import logging

from ..http_client import HttpClient
from ..resolver import EntityResolver
from . import record

log = logging.getLogger("pipeline.startups")

SOURCE_NAME = "Y Combinator"
DIRECTORY_URL = "https://yc-oss.github.io/api/companies/all.json"
AI_TAG_HINTS = (
    "artificial intelligence", "machine learning", "generative ai", "llm",
    "nlp", "computer vision", "deep learning", "ai infrastructure", "ai assistant",
)


def _is_ai(company: dict) -> bool:
    blob = " ".join(company.get("tags") or []).lower()
    blob += " " + (company.get("one_liner") or "").lower()
    return any(hint in blob for hint in AI_TAG_HINTS)


async def fetch_startups(http: HttpClient, resolver: EntityResolver, target: int = 1100) -> list[dict]:
    companies = await http.get(DIRECTORY_URL, expect="json")
    if not isinstance(companies, list):
        raise RuntimeError("YC directory returned an unexpected payload")
    log.info("YC directory: %d companies downloaded in one request", len(companies))

    ai_first = sorted(
        companies,
        key=lambda c: (0 if _is_ai(c) else 1, -(c.get("team_size") or 0)),
    )
    records, seen = [], set()
    for company in ai_first:
        slug = company.get("slug")
        name = (company.get("name") or "").strip()
        if not slug or not name or slug in seen:
            continue
        seen.add(slug)
        team_size = company.get("team_size")
        records.append(
            record(
                "STARTUP",
                SOURCE_NAME,
                f"https://www.ycombinator.com/companies/{slug}",
                {
                    "entityName": resolver.resolve(name, "startups"),
                    "data": {
                        "employeeCount": int(team_size) if isinstance(team_size, (int, float)) and team_size else None,
                    },
                },
            )
        )
        if len(records) >= target:
            break
    log.info("startups: %d records (%d AI-tagged first)", len(records), sum(1 for c in ai_first[: len(records)] if _is_ai(c)))
    return records
