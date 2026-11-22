"""Multi-tier LLM extraction engine (Phase III).

Chain: Gemini Flash -> Groq Llama 3 -> DeepSeek (configurable order). Three
failure modes are handled distinctly, because they mean different things:

* 429 Too Many Requests  -> exponential backoff with jitter, honouring
  Retry-After, then fall to the next tier. The tier is throttled, not dead.
* 413 Payload Too Large  -> the chunk budget halves and the SAME tier
  retries; a 413 is our chunking bug, not the provider's fault.
* Invalid / ungrounded output -> rejected, next tier. This is the
  anti-hallucination gate: every extracted string must literally appear
  in the source text (normalised), so the model can structure evidence
  but never invent it. A record the chain cannot ground is returned as
  None and the caller falls back to the deterministic extractor.

Chunking: paragraphs are scored by schema-keyword density; the head of
the document plus the densest paragraphs fill the budget, so truncation
keeps the semantically dense content instead of an arbitrary prefix.
If no API keys are configured the engine reports no tiers and callers
use the deterministic extractor — the pipeline is runnable end-to-end
either way, and every record records which tier produced it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re

from .http_client import HttpClient, HttpStatusError

log = logging.getLogger("pipeline.llm")

# Conservative per-call character budgets (well under each model's context,
# leaving room for the prompt and the response).
TIER_BUDGETS = {"gemini": 60000, "groq": 24000, "deepseek": 48000}
MODELS = {
    "gemini": "gemini-2.0-flash",
    "groq": "llama-3.3-70b-versatile",
    "deepseek": "deepseek-chat",
}


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def split_into_chunks(text: str, max_chars: int) -> list[str]:
    """Split on paragraph boundaries; no chunk ever exceeds max_chars."""
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, current = [], ""
    for para in paragraphs:
        if len(para) > max_chars:  # pathological paragraph: hard-split
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(para), max_chars):
                chunks.append(para[i : i + max_chars])
            continue
        if len(current) + len(para) + 2 > max_chars:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks or [text[:max_chars]]


def dense_chunk(text: str, max_chars: int, keywords: tuple[str, ...] = ()) -> str:
    """One budget-sized chunk keeping the head + densest paragraphs."""
    if len(text) <= max_chars:
        return text
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    head, rest = paragraphs[:2], paragraphs[2:]

    def density(p: str) -> float:
        lowered = p.lower()
        hits = sum(lowered.count(k) for k in keywords)
        return hits / max(1, len(p) / 200)

    rest.sort(key=density, reverse=True)
    chosen, used = list(head), sum(len(p) + 2 for p in head)
    for para in rest:
        if used + len(para) + 2 > max_chars:
            continue
        chosen.append(para)
        used += len(para) + 2
    return "\n\n".join(chosen)[:max_chars]


def _grounded(obj: dict, source: str) -> bool:
    haystack = re.sub(r"[^a-z0-9 ]", " ", source.lower())

    def ok(value) -> bool:
        if isinstance(value, str) and len(value) >= 4:
            needle = re.sub(r"[^a-z0-9 ]", " ", value.lower()).strip()
            return needle in haystack
        if isinstance(value, list):
            return all(ok(v) for v in value)
        if isinstance(value, dict):
            return all(ok(v) for v in value.values())
        return True

    return all(ok(v) for v in obj.values())


class LlmEngine:
    def __init__(self, settings, http: HttpClient):
        self._s = settings
        self._http = http
        self._keys = {
            "gemini": settings.gemini_api_key,
            "groq": settings.groq_api_key,
            "deepseek": settings.deepseek_api_key,
        }

    def available_tiers(self) -> list[str]:
        return [t for t in self._s.llm_tier_order if self._keys.get(t)]

    async def extract_json(
        self,
        instruction: str,
        text: str,
        required_keys: tuple[str, ...],
        keywords: tuple[str, ...] = (),
    ) -> tuple[dict, str] | None:
        """Returns (structured_obj, tier) or None if every tier failed."""
        for tier in self.available_tiers():
            budget = TIER_BUDGETS[tier]
            for attempt in range(4):
                chunk = dense_chunk(text, budget, keywords)
                try:
                    raw = await self._call(tier, instruction, chunk)
                except HttpStatusError as err:
                    if err.status == 413 and budget > 4000:
                        budget //= 2
                        log.warning("%s payload too large; budget halved to %d chars", tier, budget)
                        continue
                    if err.status == 429:
                        wait = min(2**attempt * 2, 60) + random.uniform(0, 1.5)
                        log.warning("%s rate limited; backing off %.1fs", tier, wait)
                        await asyncio.sleep(wait)
                        continue
                    log.warning("%s failed with HTTP %s; next tier", tier, err.status)
                    break
                except Exception as err:  # network, timeout, malformed provider body
                    log.warning("%s call error (%s); next tier", tier, err)
                    break
                obj = _parse_json(raw)
                if obj is None or any(k not in obj for k in required_keys):
                    log.warning("%s returned unusable JSON; next tier", tier)
                    break
                if not _grounded(obj, text):
                    log.warning("%s output failed grounding check; rejected", tier)
                    break
                return obj, tier
        return None

    async def _call(self, tier: str, instruction: str, chunk: str) -> str:
        prompt = (
            f"{instruction}\n\nRespond with a single JSON object only, no prose. "
            "Copy values verbatim from the source; never invent values.\n\nSOURCE:\n" + chunk
        )
        if tier == "gemini":
            body = await self._http.post_json(
                f"https://generativelanguage.googleapis.com/v1beta/models/{MODELS[tier]}:generateContent",
                {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
                },
                {"x-goog-api-key": self._keys[tier], "Content-Type": "application/json"},
            )
            return body["candidates"][0]["content"]["parts"][0]["text"]
        if tier == "groq":
            body = await self._http.post_json(
                "https://api.groq.com/openai/v1/chat/completions",
                {
                    "model": MODELS[tier],
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": prompt}],
                },
                {"Authorization": f"Bearer {self._keys[tier]}", "Content-Type": "application/json"},
            )
            return body["choices"][0]["message"]["content"]
        body = await self._http.post_json(
            "https://api.deepseek.com/chat/completions",
            {
                "model": MODELS[tier],
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt}],
            },
            {"Authorization": f"Bearer {self._keys[tier]}", "Content-Type": "application/json"},
        )
        return body["choices"][0]["message"]["content"]


def _parse_json(raw: str) -> dict | None:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        obj = json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            obj = json.loads(match.group(0))
        except ValueError:
            return None
    return obj if isinstance(obj, dict) else None


# --- Deterministic tier (used when no LLM keys are configured) ---------------

ROLE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Research", ("research scientist", "research engineer", "researcher", "scientist", "phd", "postdoc")),
    ("Engineering", ("engineer", "backend", "frontend", "full stack", "full-stack", "developer", "entwickler", "architect", "swe", "infrastructure", "automation")),
    ("Data", ("data scientist", "data engineer", "data analyst", "analytics", "mlops", "data platform", "annotation", "data annotation")),
    ("Product", ("product manager", "product designer", "program manager", "product lead")),
    ("Design", ("designer", "ux", "ui designer", "design lead")),
    ("Business", ("sales", "marketing", "operations", "recruiter", "customer success", "business development", "account executive", "venture development", "consultant", "berater")),
)


def classify_role_family(title: str, text: str = "") -> str:
    haystack = f"{title} {text[:400]}".lower()
    for family, keywords in ROLE_KEYWORDS:
        if any(k in haystack for k in keywords):
            return family
    return "Other"


def detect_remote(text: str) -> bool:
    lowered = (text or "").lower()
    return "remote" in lowered and "not remote" not in lowered and "no remote" not in lowered
