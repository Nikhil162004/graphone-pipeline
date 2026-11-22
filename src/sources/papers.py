"""Phase I, research papers: Hugging Face Papers, walked day by day.

Papers With Code (the brief's example source) was sunset by Meta in
2025 and its API no longer answers, so the correlation it pioneered —
paper -> canonical GitHub repository -> live star count — is sourced
from HF Papers instead, whose records carry `githubRepo` and
`githubStars` natively. Stars are therefore HF's recorded counts at
collection time (spot-verified against GitHub; the pipeline refreshes
exactly via the GitHub REST API when GITHUB_TOKEN is configured).
The walk goes backwards from yesterday until the target is met, which
is also the incremental pattern a daily job would use. arXiv's API is
the overflow source if the walk ever comes up short: same record
shape, github fields empty rather than invented.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from ..http_client import HttpClient, HttpStatusError
from . import record

log = logging.getLogger("pipeline.papers")

SOURCE_NAME = "Hugging Face Papers"
DAILY = "https://huggingface.co/api/daily_papers"
ARXIV = "http://export.arxiv.org/api/query"
ATOM = {"a": "http://www.w3.org/2005/Atom"}


async def fetch_papers(http: HttpClient, target: int = 1100, max_days: int = 120) -> list[dict]:
    by_id: dict[str, dict] = {}
    day = datetime.now(timezone.utc).date() - timedelta(days=1)
    for _ in range(max_days):
        if len(by_id) >= target * 2:  # headroom to rank by repo coverage
            break
        try:
            items = await http.get(f"{DAILY}?date={day.isoformat()}&limit=100", expect="json")
        except HttpStatusError as err:
            log.warning("daily_papers %s failed: %s", day, err)
            items = []
        if isinstance(items, list):
            for item in items:
                paper = item.get("paper") or {}
                if paper.get("id"):
                    by_id[paper["id"]] = paper
        day -= timedelta(days=1)
    log.info("papers: %d unique papers from %d days of HF Papers", len(by_id), max_days)

    papers = sorted(
        by_id.values(),
        key=lambda p: (0 if p.get("githubRepo") else 1, -(p.get("githubStars") or 0)),
    )
    records = [_to_record(p) for p in papers[:target]]

    if len(records) < 1000:
        log.warning("papers below target after HF walk (%d); topping up from arXiv", len(records))
        records += await _arxiv_topup(http, 1000 - len(records), set(by_id))
    log.info("papers: %d records (%d with GitHub repos)", len(records),
             sum(1 for r in records if r["content"].get("github_url")))
    return records


def _to_record(paper: dict) -> dict:
    arxiv_id = paper["id"]
    return record(
        "RESEARCH_PAPER",
        SOURCE_NAME,
        f"https://arxiv.org/abs/{arxiv_id}",
        {
            "title": (paper.get("title") or "").strip(),
            "authors": [a.get("name", "") for a in paper.get("authors", []) if a.get("name")],
            "paper_url": f"https://arxiv.org/abs/{arxiv_id}",
            "github_url": paper.get("githubRepo") or "",
            "github_stars": paper.get("githubStars") if isinstance(paper.get("githubStars"), int) else "",
            "published_date": paper.get("publishedAt") or "",
        },
    )


async def _arxiv_topup(http: HttpClient, needed: int, seen: set) -> list[dict]:
    url = (
        f"{ARXIV}?search_query=cat:cs.AI+OR+cat:cs.LG+OR+cat:cs.CL"
        f"&start=0&max_results={needed}&sortBy=submittedDate&sortOrder=descending"
    )
    try:
        xml = await http.get(url)
        root = ET.fromstring(xml)
    except Exception as err:  # top-up is best-effort by design
        log.warning("arXiv top-up failed: %s", err)
        return []
    records = []
    for entry in root.findall("a:entry", ATOM):
        abs_url = (entry.findtext("a:id", default="", namespaces=ATOM) or "").strip()
        arxiv_id = abs_url.rsplit("/", 1)[-1].split("v")[0]
        if not arxiv_id or arxiv_id in seen:
            continue
        seen.add(arxiv_id)
        records.append(
            record(
                "RESEARCH_PAPER",
                "arXiv",
                abs_url,
                {
                    "title": " ".join((entry.findtext("a:title", default="", namespaces=ATOM) or "").split()),
                    "authors": [a.findtext("a:name", default="", namespaces=ATOM) for a in entry.findall("a:author", ATOM)],
                    "paper_url": abs_url,
                    "github_url": "",
                    "github_stars": "",
                    "published_date": entry.findtext("a:published", default="", namespaces=ATOM) or "",
                },
            )
        )
    return records
