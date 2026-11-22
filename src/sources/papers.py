"""Phase I, research papers: Hugging Face Papers, walked day by day.

Bulk correlation paper -> canonical GitHub repository -> star count
comes from HF Papers, whose records carry `githubRepo` and
`githubStars` natively and at scale. The walk goes backwards from
yesterday until the target is met, which is also the incremental
pattern a daily job would use; arXiv's API is the overflow source if
the walk ever comes up short (same record shape, github fields empty
rather than invented).

Separately, the brief's literal example task is executed too:
`fetch_pwc_example` scrapes `https://paperswithcode.co/paper/98456`
with the same Phase V stack (the page answers readable requests; its
old API does not) and refreshes the chosen repo's star count exactly
against the GitHub REST API. HF's recorded counts are spot-verified
against GitHub; with GITHUB_TOKEN configured the same REST refresh is
available for the whole tab.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from ..config import settings
from ..http_client import HttpClient, HttpStatusError
from . import record

log = logging.getLogger("pipeline.papers")

SOURCE_NAME = "Hugging Face Papers"
DAILY = "https://huggingface.co/api/daily_papers"
ARXIV = "http://export.arxiv.org/api/query"
ATOM = {"a": "http://www.w3.org/2005/Atom"}

EXAMPLE_PAPER_URL = "https://paperswithcode.co/paper/98456"
_REPO_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/?$")
_NOISE_REPOS = {"features", "topics", "sponsors", "about", "orgs"}


async def fetch_papers(http: HttpClient, target: int = 1100, max_days: int = 120) -> list[dict]:
    by_id: dict[str, dict] = {}
    first_day = datetime.now(timezone.utc).date() - timedelta(days=1)
    days = [first_day - timedelta(days=i) for i in range(max_days)]
    for start in range(0, max_days, 8):  # a week of days in flight at once
        if len(by_id) >= target * 2:  # headroom to rank by repo coverage
            break
        results = await asyncio.gather(
            *(_fetch_day(http, d) for d in days[start : start + 8]), return_exceptions=True
        )
        for items in results:
            if isinstance(items, list):
                for item in items:
                    paper = item.get("paper") or {}
                    if paper.get("id"):
                        by_id[paper["id"]] = paper
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


async def _fetch_day(http: HttpClient, day) -> list:
    try:
        items = await http.get(f"{DAILY}?date={day.isoformat()}&limit=100", expect="json")
        return items if isinstance(items, list) else []
    except HttpStatusError as err:
        log.warning("daily_papers %s failed: %s", day, err)
        return []


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


def _title_tokens(title: str) -> set[str]:
    return {t.lower() for t in re.findall(r"[A-Za-z0-9]+(?:\.[0-9]+)?", title) if len(t) > 1 or t.isdigit()}


def _choose_official_repo(title: str, repos: list[str]) -> str:
    """Pick the linked repo that best matches the paper's title.

    Zero overlap means no claim: attaching a popular library that merely
    happens to be linked would be fabrication, so the record then keeps
    an empty github_url instead of a confident-looking wrong one.
    """
    tokens = _title_tokens(title)
    def score(repo: str) -> int:
        owner, name = repo.split("/", 1)
        return len(tokens & _title_tokens(f"{owner} {name.replace('-', ' ')}"))
    best = max(repos, key=score, default="")
    return best if best and score(best) > 0 else ""


def _json_ld(soup: BeautifulSoup) -> dict:
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        for item in data.get("@graph", [data]) if isinstance(data, dict) else []:
            if isinstance(item, dict) and item.get("headline"):
                return item
    return {}


async def fetch_pwc_example(http: HttpClient) -> dict | None:
    """Scrape the brief's example task page for real.

    https://paperswithcode.co/paper/98456 answers honest requests with
    full server-rendered metadata (JSON-LD + OpenGraph) even though its
    API is gone, so the singular worked example from the brief is
    extracted directly from that page. It emits the canonical
    RESEARCH_PAPER record; the star count is refreshed exactly from the
    GitHub REST API (GITHUB_TOKEN used when configured) rather than
    read off any secondary site.
    """
    try:
        html_text = await http.get(EXAMPLE_PAPER_URL, expect="html")
    except Exception as err:  # one record must never sink a bulk run
        log.warning("pwc example fetch failed: %s", err)
        return None
    soup = BeautifulSoup(html_text, "lxml")
    ld = _json_ld(soup)
    title = (ld.get("headline") or "").strip()
    if not title:
        og = soup.find("meta", property="og:title")
        title = (og.get("content", "").rsplit(" | ", 1)[0] if og else "").strip()
    if not title:
        log.warning("pwc example: no title on %s; skipping", EXAMPLE_PAPER_URL)
        return None

    repos: dict[str, None] = {}
    for anchor in soup.find_all("a", href=True):
        match = _REPO_RE.match(anchor["href"].split("#", 1)[0].rstrip("/"))
        if match and match.group(1).split("/", 1)[0].lower() not in _NOISE_REPOS:
            repos[match.group(1)] = None
    repo = _choose_official_repo(title, list(repos))
    github_url, github_stars = (f"https://github.com/{repo}" if repo else ""), ""
    if repo:
        headers = {"Accept": "application/vnd.github+json"}
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"
        try:
            info = await http.get(f"https://api.github.com/repos/{repo}", expect="json", headers=headers)
            if isinstance(info, dict):
                github_url = info.get("html_url") or github_url
                if isinstance(info.get("stargazers_count"), int):
                    github_stars = info["stargazers_count"]
        except Exception as err:  # stars stay "" rather than invented
            log.warning("pwc example: GitHub API failed for %s: %s", repo, err)
    log.info(
        "pwc example: %r repo=%s stars=%s",
        title[:60], github_url or "(none linked)", github_stars if github_stars != "" else "(unavailable)",
    )
    return record(
        "RESEARCH_PAPER",
        "Papers With Code",
        EXAMPLE_PAPER_URL,
        {
            "title": title,
            "authors": [a.get("name", "") for a in ld.get("author", []) if isinstance(a, dict) and a.get("name")],
            "paper_url": EXAMPLE_PAPER_URL,
            "github_url": github_url,
            "github_stars": github_stars,
            "published_date": ld.get("datePublished") or "",
            "abstract": (ld.get("abstract") or "").strip(),
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
