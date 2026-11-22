# FrontierAtlas / GraphOne — Intelligence Ingestion Pipeline

AI Engineer assessment submission: a scalable, fault-tolerant ingestion pipeline that
acquires startups, products, research papers, AI jobs and AI news; normalises them into
canonical schemas; resolves messy names to canonical entities; and exports a six-tab
workbook in which **every record traces to a live source URL**. No generated data
anywhere: if a value is in the output, a source said it.

## Results from the reference run (2026-11-22)

| Tab | Rows | Guarantee |
|---|---|---|
| Startups | **1,100** | unique companies, canonical names, source URL each |
| Products | **1,100** | unique AI products, canonical owner, pricing enum |
| Research Papers | **1,100** | **1,100/1,100 with GitHub repo + star count** |
| Jobs | 28 | every date provably inside the last 24 h (61 stale dropped) |
| News | 6 | every date provably inside the last 24 h, full text stored |
| Entity Mapping Log | 1,708 | raw → canonical, with method and match score |

Volumes for Jobs/News are whatever the last 24 hours genuinely contained (this run was a
Sunday); the freshness guarantee is prioritised over headline counts, deliberately.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # optional: add LLM keys (see below)
python -m src.pipeline          # Phase I + II + export (~30 s)
python -m pytest tests/ -q      # 16 unit tests: dates, resolver, chunking
```

Outputs land in `output/`: `workbook.xlsx` (the six tabs), `csv/` (same data),
`records.json`, and `run_summary.json` (per-source yields and drop counts).
`python -m src.pipeline phase1|phase2` runs one phase; `export` re-exports.

**LLM keys are optional.** With no keys the pipeline runs on its deterministic tier
(JSON-LD/meta/regex extraction, keyword classifiers) and still produces the full output.
Set `GEMINI_API_KEY`, `GROQ_API_KEY`, `DEEPSEEK_API_KEY` in `.env` to enable the
multi-tier LLM chain (order via `LLM_TIER_ORDER`); every record then names the tier that
produced it. `GITHUB_TOKEN` enables exact live star refreshes via the GitHub REST API.

## How it maps to the brief

**Phase I — bulk acquisition.** One async codebase; connectors prefer the bulk/structured
route a source already offers. Startups come from the Y Combinator directory as a single
bulk JSON document (~5k companies in one request; AI-tagged companies ranked first).
Products are Hugging Face Spaces (the largest public directory of live AI products),
merged from a top-by-likes query plus nine topical searches and de-duplicated. Papers
come from **Hugging Face Papers walked day-by-day** — Papers With Code, the brief's
example source, was sunset by Meta in 2025 and its API no longer answers, so the
paper → GitHub repo → star-count correlation it pioneered is sourced from HF records,
which carry `githubRepo` and `githubStars` natively. Stars are HF's recorded counts,
spot-verified against live GitHub (sampled repos agreed within ±8 stars; the difference
is repos gaining stars between snapshots, and the exact-refresh path is the GitHub API
when a token is configured). The walk pattern (backwards from yesterday, resume by
cursor) is the same one a daily job uses. The architecture scales to 500k+ by
configuration — queue-partitioned cursors per host — see `architecture.pdf` §2.

**Phase II — fresh signals.** Five news sources (TechCrunch AI, OpenAI News, Hugging
Face Blog, The Verge AI, MIT Technology Review AI — VentureBeat's feed returned 429
during development and was swapped out by configuration, not code) and five job boards
(RemoteOK, Remotive, Arbeitnow, Jobicy, We Work Remotely), all via official APIs/RSS.
Dates are normalised from ISO, RFC-2822, epoch and relative ("2 hours ago") formats. A
record is kept only if its proven date is inside the 24 h window; undated records are
dropped and counted (jobs this run: 61 stale dropped, 0 undated kept). The one exception
is labelled: feeds that omit dates can pass a **first-seen heuristic** (unseen URL among
the feed's most recent items, tracked in a persistent seen-store) and are marked
`freshnessBasis='first-seen'`. Articles and postings are fetched for full text
(boilerplate/sidebar stripping included — sidebar headlines were measurably
contaminating entity mentions before that fix). De-duplication is by source URL, then by
(company, title) fingerprint, so syndicated jobs appear once.

**Phase III — LLM extraction engine** (`src/llm_engine.py`). Fallback chain
Gemini Flash → Groq Llama 3 → DeepSeek. 413s are prevented by per-tier character budgets
and a chunker that keeps the document head plus the highest keyword-density paragraphs;
if one still occurs, the budget halves and the same tier retries. 429s get exponential
backoff with jitter honouring Retry-After, then the chain falls through. Every extracted
object passes a **grounding gate** — extracted strings must literally occur in the source
text — before acceptance, which is the structural answer to the hallucination rule: the
model may structure evidence, never invent it. Without keys, deterministic extraction is
the final tier, so the pipeline never depends on a provider being up.

**Phase IV — entity resolution** (`src/resolver.py`). Deterministic, auditable, never
an LLM guess: exact canonical → seeded alias → legal-suffix stripping ("OpenAI, Inc." →
OpenAI) → squashed-form equality ("Open AI" → OpenAI) → difflib fuzzy at ≥ 0.88. The seed
is 51 canonical AI companies with curated aliases (`data/seed_entities.json`). Every
resolution is logged; the log is the sixth tab.

**Phase V — anti-bot & async.** Everything is asyncio + aiohttp behind one HTTP layer:
global and per-host concurrency semaphores, bounded retries (429/5xx/timeouts only;
403/404 are terminal), one honest User-Agent. Strategy is to need as little bypass as
possible — APIs and feeds first; Playwright rendering pool for JS-only sources; for
Cloudflare/DataDome-protected sources the escalation is official API → partnership →
licensed data, not captcha-solving or fingerprint spoofing (`architecture.pdf` §6).

**Phase VI — architecture.** `architecture.pdf` (2 pages): scale strategy with
throughput math, the exact 413/429 playbook, exactly-once freshness across distributed
nodes (pre-fetch `SET NX` fingerprint claims), and storage justification (PostgreSQL as
system of record, pgvector → Qdrant for vectors, graph as a later read-side projection).

## Schema notes

Tabs use the brief's field names as dotted column headers. Two additive extensions,
both forced by usefulness rather than invention: Products add `content.data.productName`
and `content.data.likes` (a product record must say which product it is), and Jobs add
`content.title` and `content.fullText` (Phase II requires full-text capture). Nothing in
the brief's schemas was renamed or dropped.

## Integrity rules this codebase keeps

- No record without a fetched source; no field value the source did not state.
- Pricing is `FREE` for all HF Spaces because Spaces are free to run; the other enum
  values await a paid-marketplace connector (interface-ready, not populated by guessing).
- Freshness is proven by dates or explicitly labelled as heuristic.
- Failed sources degrade the run's volume, never its correctness: per-source guards log,
  count, and continue.

## Repository layout

```
README.md                  this file
architecture.pdf           Phase VI design document (2 pages)
requirements.txt  .env.example
data/seed_entities.json    51 canonical entities + aliases
src/
  pipeline.py              orchestrator (phase1 / phase2 / export)
  config.py  dates.py  http_client.py  text_extract.py
  llm_engine.py            fallback chain, chunking, grounding, deterministic tier
  resolver.py              entity resolution + mapping log
  sources/                 startups, products, papers, jobs, news connectors
  export_workbook.py       six-tab workbook + CSV mirror
  make_architecture_pdf.py regenerates architecture.pdf
tests/                     dates, resolver, chunking (16 tests)
output/                    workbook.xlsx, csv/, records.json, run_summary.json
```
