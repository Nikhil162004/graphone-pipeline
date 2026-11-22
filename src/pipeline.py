"""Pipeline orchestrator.

    python -m src.pipeline            # everything: Phase I + II + export
    python -m src.pipeline phase1     # bulk: startups, products, papers
    python -m src.pipeline phase2     # fresh: jobs, news
    python -m src.pipeline export     # re-export the last records.json

Every run writes output/records.json, output/workbook.xlsx,
output/csv/*.csv and output/run_summary.json, and logs each source's
yield and each drop decision. Records are produced exclusively from
live source responses; nothing in the output is generated content.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import settings
from src.export_workbook import TAB_FOR_TYPE, write_outputs
from src.http_client import HttpClient
from src.llm_engine import LlmEngine
from src.resolver import EntityResolver
from src.sources.jobs import fetch_jobs
from src.sources.news import fetch_news
from src.sources.papers import fetch_papers
from src.sources.products import fetch_products
from src.sources.startups import fetch_startups

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = os.path.join(ROOT, "data", "seed_entities.json")


def _mapping_rows(resolver: EntityResolver) -> list[dict]:
    counts = Counter((e["rawName"], e["canonicalName"], e["method"], e["vertical"]) for e in resolver.log)
    scores = {(e["rawName"], e["canonicalName"], e["vertical"]): e["score"] for e in resolver.log}
    return [
        {
            "rawName": raw, "canonicalName": canonical, "method": method,
            "score": scores.get((raw, canonical, vertical), ""), "vertical": vertical,
            "occurrences": count,
        }
        for (raw, canonical, method, vertical), count in sorted(counts.items())
    ]


async def _run(stage: str) -> dict:
    resolver = EntityResolver(SEED)
    records: list[dict] = []
    async with HttpClient(settings.user_agent, settings.global_concurrency, settings.per_host_concurrency) as http:
        engine = LlmEngine(settings, http)
        logging.getLogger("pipeline").info(
            "LLM tiers available: %s", engine.available_tiers() or "none (deterministic tier)"
        )
        if stage in ("all", "phase1"):
            records += await fetch_startups(http, resolver)
            records += await fetch_products(http, resolver)
            records += await fetch_papers(http)
        if stage in ("all", "phase2"):
            records += await fetch_jobs(http, resolver, settings.freshness_hours)
            records += await fetch_news(http, resolver, engine, settings.freshness_hours)
        http_stats = dict(http.stats)
    by_tab: dict[str, list[dict]] = {}
    for rec in records:
        by_tab.setdefault(TAB_FOR_TYPE[rec["recordType"]], []).append(rec)
    return {"records": records, "by_tab": by_tab, "mapping": _mapping_rows(resolver), "http": http_stats}


def main() -> None:
    parser = argparse.ArgumentParser(description="FrontierAtlas ingestion pipeline")
    parser.add_argument("stage", nargs="?", default="all", choices=["all", "phase1", "phase2", "export"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    os.chdir(ROOT)
    if args.stage == "export":
        with open("output/records.json", encoding="utf-8") as fh:
            payload = json.load(fh)
        by_tab, mapping = payload["by_tab"], payload["mapping"]
        http_stats = payload.get("http", {})
    else:
        result = asyncio.run(_run(args.stage))
        by_tab, mapping, http_stats = result["by_tab"], result["mapping"], result["http"]
        os.makedirs("output", exist_ok=True)
        with open("output/records.json", "w", encoding="utf-8") as fh:
            json.dump({"by_tab": by_tab, "mapping": mapping, "http": http_stats}, fh)

    workbook = write_outputs(by_tab, mapping)
    summary = {
        "ranAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stage": args.stage,
        "tabs": {tab: len(rows) for tab, rows in by_tab.items()},
        "mappingEntries": len(mapping),
        "http": http_stats,
        "workbook": workbook,
    }
    with open("output/run_summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
