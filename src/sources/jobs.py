"""Phase II, jobs: five job boards, 24-hour freshness enforced.

Connectors: RemoteOK (API), Remotive (API), Arbeitnow (API), Jobicy
(API), We Work Remotely (RSS). All five offer structured routes, so no
HTML scraping is needed for discovery; posting pages are fetched only
for boards whose API omits the description.

The freshness guarantee is strict on purpose: a record enters the
output only if its normalised publication datetime provably sits
inside the window. Records with missing/unparseable dates are dropped
and counted — a smaller honest set beats a larger unverifiable one.
De-duplication is by source URL, then by (company, title) fingerprint,
so the same role syndicated to two boards appears once.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from ..dates import is_fresh, iso_z, normalize_datetime
from ..http_client import HttpClient, HttpStatusError
from ..llm_engine import classify_role_family, detect_remote
from ..resolver import EntityResolver
from ..text_extract import strip_html
from . import record

log = logging.getLogger("pipeline.jobs")

AI_PATTERN = re.compile(
    r"\b(ai|ml|llm|mlops|nlp|genai)\b|machine learning|deep learning|artificial intelligence|"
    r"data scien|computer vision|prompt engineer|language model|ai engineer|ml engineer",
    re.IGNORECASE,
)


def _is_ai(title: str, tags: str = "") -> bool:
    return bool(AI_PATTERN.search(f"{title} {tags}"))


async def fetch_jobs(http: HttpClient, resolver: EntityResolver, freshness_hours: int = 24) -> list[dict]:
    now = datetime.now(timezone.utc)
    raw: list[dict] = []
    for name, connector in (
        ("RemoteOK", _remoteok),
        ("Remotive", _remotive),
        ("Arbeitnow", _arbeitnow),
        ("Jobicy", _jobicy),
        ("We Work Remotely", _weworkremotely),
    ):
        try:
            items = await connector(http)
        except Exception as err:
            log.warning("%s connector failed: %s", name, err)
            items = []
        log.info("%s: %d postings downloaded", name, len(items))
        raw.extend(items)

    records, seen_urls, seen_fingerprints, dropped_stale, dropped_unknown_date = [], set(), set(), 0, 0
    for item in raw:
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        if not title or not url or not _is_ai(title, item.get("tags", "")):
            continue
        published = normalize_datetime(item.get("published"), now)
        if published is None:
            dropped_unknown_date += 1
            continue
        if not is_fresh(published, now, freshness_hours):
            dropped_stale += 1
            continue
        company = resolver.resolve(item.get("company") or "", "jobs")
        fingerprint = (company.lower(), title.lower())
        if url in seen_urls or fingerprint in seen_fingerprints:
            continue
        seen_urls.add(url)
        seen_fingerprints.add(fingerprint)
        text = item.get("text") or ""
        records.append(
            record(
                "JOB",
                item["source"],
                url,
                {
                    "title": title,
                    "company": company,
                    "date": iso_z(published),
                    "is_remote": bool(item.get("is_remote", detect_remote(text or title))),
                    "role_family": classify_role_family(title, text),
                    "fullText": text[:12000],
                },
            )
        )
    records.sort(key=lambda r: r["content"]["date"], reverse=True)
    log.info(
        "jobs: %d fresh AI jobs kept (%d stale dropped, %d undated dropped)",
        len(records), dropped_stale, dropped_unknown_date,
    )
    return records


async def _remoteok(http: HttpClient) -> list[dict]:
    data = await http.get("https://remoteok.com/api", expect="json")
    items = []
    for job in data if isinstance(data, list) else []:
        if not isinstance(job, dict) or not job.get("position") or not job.get("url"):
            continue  # first element is the API's legal notice, not a job
        items.append({
            "source": "RemoteOK", "title": job["position"], "company": job.get("company", ""),
            "url": job["url"], "published": job.get("date"), "is_remote": True,
            "tags": " ".join(job.get("tags") or []), "text": strip_html(job.get("description", "")),
        })
    return items


async def _remotive(http: HttpClient) -> list[dict]:
    items = []
    jobs = []
    for category in (None, "software-dev", "data"):
        url = "https://remotive.com/api/remote-jobs?limit=100"
        if category:
            url += f"&category={category}"
        data = await http.get(url, expect="json")
        jobs += (data or {}).get("jobs", [])
    for job in jobs:
        items.append({
            "source": "Remotive", "title": job.get("title", ""), "company": job.get("company_name", ""),
            "url": job.get("url", ""), "published": job.get("publication_date"), "is_remote": True,
            "tags": " ".join(job.get("tags") or []), "text": strip_html(job.get("description", "")),
        })
    return items


async def _arbeitnow(http: HttpClient) -> list[dict]:
    items = []
    jobs = []
    for page in (1, 2, 3):
        data = await http.get(f"https://www.arbeitnow.com/api/job-board-api?page={page}", expect="json")
        jobs += (data or {}).get("data", [])
    for job in jobs:
        items.append({
            "source": "Arbeitnow", "title": job.get("title", ""), "company": job.get("company_name", ""),
            "url": job.get("url", ""), "published": job.get("created_at"),
            "is_remote": bool(job.get("remote")), "tags": " ".join(job.get("tags") or []),
            "text": strip_html(job.get("description", "")),
        })
    return items


async def _jobicy(http: HttpClient) -> list[dict]:
    data = await http.get("https://jobicy.com/api/v2/remote-jobs?count=50", expect="json")
    items = []
    for job in (data or {}).get("jobs", []):
        items.append({
            "source": "Jobicy", "title": job.get("jobTitle", ""), "company": job.get("companyName", ""),
            "url": job.get("url", ""), "published": job.get("pubDate"), "is_remote": True,
            "tags": job.get("jobIndustry", ""), "text": strip_html(job.get("jobDescription", "")),
        })
    return items


async def _weworkremotely(http: HttpClient) -> list[dict]:
    items = []
    for feed in (
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-data-jobs.rss",
    ):
        try:
            xml = await http.get(feed)
        except HttpStatusError:
            continue
        items += _wwr_items(ET.fromstring(xml))
    # Full text for the survivors only (AI keyword in the title): fetch the
    # posting page and replace the RSS summary with the page body.
    for item in [i for i in items if _is_ai(i["title"])]:
        try:
            from ..text_extract import extract_full_text

            html = await http.get(item["url"])
            item["text"] = extract_full_text(html)["text"] or item["text"]
        except HttpStatusError:
            pass
    return items


def _wwr_items(root) -> list[dict]:
    items = []
    for node in root.iter("item"):
        raw_title = (node.findtext("title") or "").strip()
        company, _, title = raw_title.partition(": ")
        link = (node.findtext("link") or "").strip()
        if not link:
            continue
        items.append({
            "source": "We Work Remotely", "title": title or raw_title,
            "company": company if title else "", "url": link,
            "published": node.findtext("pubDate"), "is_remote": True, "tags": "",
            "text": strip_html(node.findtext("description") or ""),
        })
    return items
