"""Deterministic entity resolution (Phase IV).

"OpenAI", "OpenAI, Inc." and "Open AI" must land on one canonical record,
and the mapping must be auditable, so resolution is rule-based with a
seeded alias table — never an LLM guess. Every resolution is appended to
a log that becomes the Entity Mapping Log tab of the deliverable.

Order of attempts: exact canonical → known alias → legal-form stripping
("Acme, Inc." → "Acme") → squashed-form equality ("openai" == "Open AI")
→ fuzzy (difflib ratio on squashed forms, threshold 0.88). Anything else
passes through cleaned but unresolved, and says so in the log.
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher

_LEGAL_SUFFIXES = {
    "inc", "llc", "ltd", "corp", "corporation", "company", "co", "gmbh",
    "sarl", "bv", "pte", "pty", "kk", "sa", "ag", "oy", "ab",
}
_PUNCT = re.compile(r"[^a-z0-9 ]+")


def clean_name(raw: str) -> str:
    return re.sub(r"\s+", " ", (raw or "").strip())


def squash(name: str) -> str:
    """Lowercase, punctuation-free, space-free comparison form."""
    text = _PUNCT.sub(" ", name.lower())
    tokens = [t for t in text.split() if t not in _LEGAL_SUFFIXES]
    return "".join(tokens)


class EntityResolver:
    def __init__(self, seed_path: str):
        with open(seed_path, encoding="utf-8") as fh:
            seed = json.load(fh)
        self.canonicals: list[str] = []
        self._alias_to_canonical: dict[str, str] = {}
        self._squashed_to_canonical: dict[str, str] = {}
        for entry in seed:
            canonical = entry["canonical"]
            self.canonicals.append(canonical)
            self._squashed_to_canonical[squash(canonical)] = canonical
            for alias in entry.get("aliases", []):
                self._alias_to_canonical[squash(alias)] = canonical
        self.log: list[dict] = []

    def resolve(self, raw: str, vertical: str = "") -> str:
        raw_clean = clean_name(raw)
        if not raw_clean:
            return ""
        key = squash(raw_clean)
        canonical, method, score = raw_clean, "unresolved", 0.0

        if key in self._squashed_to_canonical:
            canonical, method, score = self._squashed_to_canonical[key], "exact", 1.0
        elif key in self._alias_to_canonical:
            canonical, method, score = self._alias_to_canonical[key], "alias", 1.0
        else:
            best_name, best_ratio = "", 0.0
            for candidate_key, candidate in self._squashed_to_canonical.items():
                ratio = SequenceMatcher(None, key, candidate_key).ratio()
                if ratio > best_ratio:
                    best_name, best_ratio = candidate, ratio
            if best_ratio >= 0.88:
                canonical, method, score = best_name, "fuzzy", round(best_ratio, 3)

        self.log.append(
            {
                "rawName": raw_clean,
                "canonicalName": canonical,
                "method": method,
                "score": score,
                "vertical": vertical,
            }
        )
        return canonical

    def find_mentions(self, text: str) -> list[str]:
        """Canonical seed entities mentioned in free text (used for news
        entity tagging without an LLM). Squashed substring scan."""
        haystack = squash(text or "")
        return sorted(
            {c for k, c in self._squashed_to_canonical.items() if len(k) >= 4 and k in haystack}
        )
