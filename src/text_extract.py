"""Full-text and metadata extraction from HTML pages (Phase II).

A small, deterministic reader: strip chrome (nav/footer/forms/scripts),
prefer <article>, otherwise take the container whose paragraphs carry the
most text. Also surfaces machine-readable metadata (JSON-LD, OpenGraph,
<time>) which the deterministic extractor and the date normaliser use
before any LLM is consulted.
"""
from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

_JUNK = ["script", "style", "nav", "footer", "header", "aside", "form", "noscript", "iframe", "svg"]
# Sidebar/related-content widgets carry OTHER stories' headlines; left in,
# they contaminate both the stored full text and entity mentions.
_BOILERPLATE = re.compile(
    r"related|popular|sidebar|widget|promo|trending|newsletter|recirc|most-read|recommended", re.IGNORECASE
)


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    return extract_from_soup(soup)["text"]


def extract_from_soup(soup: BeautifulSoup) -> dict:
    for tag in soup(_JUNK):
        tag.decompose()
    doomed = [
        tag
        for tag in soup.find_all(True)
        if _BOILERPLATE.search(" ".join(tag.get("class") or []))
        or _BOILERPLATE.search(tag.get("id") or "")
    ]
    for tag in reversed(doomed):  # children first; ancestors may already be gone
        try:
            tag.decompose()
        except Exception:
            pass

    title = ""
    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()

    published_raw = ""
    for selector in (
        {"property": "article:published_time"},
        {"name": "article:published_time"},
        {"name": "date"},
        {"name": "pubdate"},
    ):
        meta = soup.find("meta", attrs=selector)
        if meta and meta.get("content"):
            published_raw = meta["content"].strip()
            break
    if not published_raw:
        time_tag = soup.find("time")
        if time_tag and time_tag.get("datetime"):
            published_raw = time_tag["datetime"].strip()
    if not published_raw:
        published_raw = _json_ld_date(soup) or ""

    description = ""
    og_desc = soup.find("meta", attrs={"property": "og:description"}) or soup.find(
        "meta", attrs={"name": "description"}
    )
    if og_desc and og_desc.get("content"):
        description = og_desc["content"].strip()

    container = soup.find("article") or soup.find("main") or _densest_container(soup) or soup.body or soup
    paragraphs = [p.get_text(" ", strip=True) for p in container.find_all("p")]
    paragraphs = [p for p in paragraphs if len(p) >= 40]
    text = "\n\n".join(paragraphs)
    if len(text) < 200:  # sparse page: fall back to all visible text
        text = soup.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return {"title": title, "published_raw": published_raw, "description": description, "text": text[:60000]}


def extract_full_text(html: str) -> dict:
    return extract_from_soup(BeautifulSoup(html, "lxml"))


def _densest_container(soup: BeautifulSoup):
    best, best_score = None, 0
    for div in soup.find_all(["div", "section"]):
        score = sum(len(p.get_text(strip=True)) for p in div.find_all("p", recursive=False))
        if score > best_score:
            best, best_score = div, score
    return best


def _json_ld_date(soup: BeautifulSoup) -> str:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("datePublished"):
                return str(item["datePublished"])
    return ""


def strip_html(fragment: str) -> str:
    """Job boards embed descriptions as HTML; store clean text."""
    if not fragment:
        return ""
    text = BeautifulSoup(fragment, "lxml").get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
