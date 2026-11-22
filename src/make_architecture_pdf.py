"""Generate architecture.pdf (max 3 pages) at the repo root.

    python -m src.make_architecture_pdf
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "architecture.pdf")

S = {
    "title": ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=15, leading=18, spaceAfter=2 * mm),
    "meta": ParagraphStyle("m", fontName="Helvetica", fontSize=8.5, leading=11, textColor="#444444", spaceAfter=4 * mm),
    "h": ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=10.5, leading=13, spaceBefore=4 * mm, spaceAfter=1.5 * mm),
    "b": ParagraphStyle("b", fontName="Helvetica", fontSize=8.8, leading=12, spaceAfter=1.5 * mm),
    "cell": ParagraphStyle("c", fontName="Helvetica", fontSize=7.8, leading=10),
    "cellb": ParagraphStyle("cb", fontName="Helvetica-Bold", fontSize=7.8, leading=10),
}


def P(text: str, style: str = "b") -> Paragraph:
    return Paragraph(text, S[style])


def table(rows: list[list[str]], widths: list[float]) -> Table:
    data = [[P(c, "cellb" if r == 0 else "cell") for c in row] for r, row in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), "#e8eaf0"),
        ("GRID", (0, 0), (-1, -1), 0.4, "#b9c0d4"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def build() -> None:
    doc = SimpleDocTemplate(OUT, pagesize=A4, topMargin=14 * mm, bottomMargin=12 * mm,
                            leftMargin=16 * mm, rightMargin=16 * mm,
                            title="FrontierAtlas Ingestion Pipeline — Architecture")
    story = [
        P("FrontierAtlas / GraphOne — Intelligence Ingestion Pipeline", "title"),
        P("AI Engineer assessment · Architecture & production design · Companion to the code in <font face='Helvetica-Bold'>src/</font> and the run evidence in <font face='Helvetica-Bold'>output/</font>", "meta"),

        P("1. System at a glance", "h"),
        P("One async Python codebase (asyncio + aiohttp) organised as <b>connectors → normalisers → "
          "entity resolution → canonical records → export</b>. Connectors exist per source, never per "
          "page-template: each implements <i>fetch → map to schema → resolve names</i> behind the same "
          "interface, so adding a source is additive. All HTTP goes through one client enforcing global "
          "and per-host concurrency semaphores, retry policy and an honest User-Agent. Records are "
          "emitted in the brief's canonical JSON schemas and flattened (dotted paths) into the six-tab "
          "workbook; every record carries <b>source.url</b>, because a record that cannot be traced to a "
          "live source page is treated as a defect, not data."),
        table([
            ["Layer", "Components (src/)", "Responsibility"],
            ["Acquire", "sources/startups.py (YC bulk JSON), products.py (HF Spaces API), papers.py (HF Papers day-walk, arXiv overflow), jobs.py (RemoteOK, Remotive, Arbeitnow, Jobicy, We Work Remotely), news.py (5 RSS feeds)", "Bulk + fresh acquisition over official APIs/feeds wherever they exist"],
            ["Normalise", "dates.py, text_extract.py, llm_engine.py", "Date normalisation (ISO/RFC-2822/epoch/relative), full-text reading, LLM structuring with grounded validation"],
            ["Resolve", "resolver.py + data/seed_entities.json", "Canonical entities: exact → alias → legal-strip → squashed → fuzzy (≥0.88), fully logged"],
            ["Serve/Export", "pipeline.py, export_workbook.py", "Orchestration, six-tab workbook + CSV mirror, run summary with drop counts"],
        ], [26 * mm, 62 * mm, 90 * mm]),

        P("2. Scale strategy: 500,000+ records with no code changes", "h"),
        P("<b>Enumeration before scraping.</b> Every vertical has a listable inventory: YC publishes the "
          "whole directory as one document; HF Spaces and Papers are cursor/day paginated; arXiv is "
          "OAI-PMH harvestable. The worker Fleet consumes <i>(source, cursor)</i> work items from a queue "
          "(SQS/Kafka in production; asyncio tasks here), partitioned by host so per-host rate limits "
          "scale out by adding hosts, not threads. At a polite 4 concurrent requests per host and "
          "~600 ms median latency, one host yields ~6.6 pages/s ≈ 570k pages/day; the 500k goal is a "
          "one-day, five-host problem, and an ongoing increment because cursors make re-runs resume "
          "instead of restart. Stateless workers + idempotent upserts (below) mean horizontal scaling "
          "is a replica count, not a refactor. Backpressure is the queue depth; per-host semaphores "
          "become distributed token buckets (Redis) at Fleet scale."),

        P("3. Handling 413s and 429s across the LLM Fleet", "h"),
        P("<b>413 is prevented, not handled:</b> each provider tier has a conservative character budget "
          "(Gemini 60k, DeepSeek 48k, Groq 24k chars). Text is chunked on paragraph boundaries and the "
          "chunker keeps the document head plus the highest keyword-density paragraphs, so payloads are "
          "semantically dense rather than truncated mid-thought; a long document becomes multiple "
          "bounded calls whose results merge. If a 413 still arrives (provider accounting differs), "
          "the budget halves and the same tier retries — it is our sizing error, not an outage. "
          "<b>429:</b> exponential backoff (2<sup>n</sup> s, capped 60 s) with full jitter, honouring "
          "Retry-After, max 4 attempts, then the chain falls to the next tier "
          "(Gemini Flash → Groq Llama 3 → DeepSeek). Every extracted field passes a <b>grounding "
          "gate</b> — extracted strings must literally occur in the source text — before acceptance; "
          "ungrounded output is rejected and the next tier tries. With no keys configured, a "
          "deterministic extractor (JSON-LD/meta/regex + keyword classifiers) is the final tier, so "
          "the pipeline is always runnable and every record names its extraction tier. Key "
          "management: one key per provider per worker pool, token-bucket per key, so the Fleet's "
          "aggregate rate stays under each provider's ceiling instead of 429ing in convoy."),

        P("4. Freshness: never processing the same article/job twice", "h"),
        P("Two independent mechanisms. <b>Date proof:</b> publication dates are normalised from ISO, "
          "RFC-2822, epoch and relative formats ('2 hours ago'); a record is 'fresh' only if its proven "
          "date sits inside the 24 h window (30 min future skew allowed). Feeds that omit dates fall "
          "back to a marked <b>first-seen heuristic</b> (unseen URL within the feed's most recent items) "
          "and are labelled <i>freshnessBasis='first-seen'</i> in the output so consumers can weigh them. "
          "<b>Exactly-once:</b> every item gets a fingerprint (SHA-256 of canonical URL; secondary "
          "(company, title) fingerprint for syndicated jobs). A seen-store maps fingerprint → first-seen "
          "timestamp with a TTL; locally it is an atomic JSON file, in production a Redis "
          "<i>SET NX</i> per fingerprint claimed <i>before</i> fetching the body — two distributed nodes "
          "racing the same article, exactly one wins the claim and the other drops it without spending "
          "a fetch. Claims older than the window expire, so a genuinely re-published item can re-enter. "
          "Drops (stale, undated, duplicate) are counted and logged per source per run; this run: 61 "
          "stale and 0 undated job postings dropped, which is the guarantee working, not failing."),

        P("5. Storage strategy", "h"),
        P("<b>PostgreSQL is the system of record.</b> The canonical entities (startups, products, "
          "papers, jobs, news) are relational with real foreign keys (product → startup, paper → repo), "
          "the access patterns are filtered scans and joins, and upsert-by-fingerprint is a natural "
          "unique-index operation. Raw payloads live in a JSONB column beside the normalised fields — "
          "reprocessing after a schema change never requires re-scraping. <b>Vectors:</b> entity and "
          "article embeddings (for fuzzy resolution assistance and semantic dedup) go in pgvector "
          "initially — one less system to operate at 500k scale — graduating to Qdrant when vector "
          "traffic justifies a dedicated service. <b>Graph:</b> the Intelligence Graph (startup ↔ "
          "founder ↔ paper ↔ repo edges) is a traversal workload Postgres serves poorly at depth; it "
          "is modelled as an edge table in Postgres now, with Neo4j as the read-side projection when "
          "multi-hop queries become a product requirement. Starting with the graph database would "
          "sacrifice transactions and upsert ergonomics for a workload that arrives later."),

        P("6. Anti-bot posture (Phase V)", "h"),
        P("The strategy is to need as little bypass as possible: (1) official APIs and feeds first — "
          "five of five job boards and five of five news sources in this build are API/RSS, where "
          "'blocking' is a rate limit to respect, not a wall to climb; (2) honest identification, "
          "per-host concurrency (4) and backoff make the crawler a good citizen that rarely triggers "
          "defences; (3) for JS-rendered sources, a Playwright pool (async API) renders pages in real "
          "browser contexts behind the same connector interface; (4) for Cloudflare/DataDome-protected "
          "high-value sources the escalation ladder is: respect robots and ToS → seek the official "
          "API or a data partnership → licensed aggregators → browser Fleet with residential egress "
          "and human-paced sessions. We do not solve captchas or spoof fingerprints: apart from the "
          "legal/ToS exposure, fingerprint arms races produce the flakiest data in the system. "
          "VentureBeat's feed returned 429 during this build; the connector set absorbed it by "
          "substituting MIT Technology Review — source lists are configuration, not code."),

        P("7. Failure modes, observability, and what this run proved", "h"),
        P("Every connector fails independently (logged, counted, skipped); retries are bounded; "
          "malformed payloads raise into per-source guards, never into the orchestrator. Runs emit "
          "structured logs plus output/run_summary.json with per-tab counts, HTTP stats and drop "
          "reasons — the numbers an on-call engineer needs at 3 a.m. Observed and absorbed in this "
          "build: Papers With Code's old API no longer answers, so bulk paper↔repo correlation comes "
          "from Hugging Face Papers, whose records carry repo URL and star count natively (HF star "
          "counts spot-verified against live GitHub within ±8 stars — HF's snapshot cadence, refreshed "
          "exactly when GITHUB_TOKEN is configured); the brief's literal example page "
          "(paperswithcode.co/paper/98456) still serves honest requests, so it is scraped directly and "
          "its repo (zai-org/GLM-5) star count refreshed exactly via the GitHub REST API (7,476 stars). "
          "Known limits: product pricing is FREE for all HF Spaces (other enum values await a paid "
          "marketplace connector); news/job volumes are whatever the last 24 h genuinely contained "
          "(6 articles, 28 jobs on a Sunday run) — the freshness guarantee is prioritised over "
          "headline counts, deliberately."),
    ]
    doc.build(story)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    build()
