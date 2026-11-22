"""Runtime settings, all environment-driven so the same code runs in the
assessment sandbox, on a laptop, or on a fleet of workers unchanged."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _load_dotenv() -> None:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str = _env("GEMINI_API_KEY")
    groq_api_key: str = _env("GROQ_API_KEY")
    deepseek_api_key: str = _env("DEEPSEEK_API_KEY")
    github_token: str = _env("GITHUB_TOKEN")
    llm_tier_order: tuple = tuple(
        t for t in (x.strip() for x in _env("LLM_TIER_ORDER", "gemini,groq,deepseek").split(",")) if t
    )
    user_agent: str = _env("SCRAPER_UA", "FrontierAtlasBot/1.0 (+assessment)")
    global_concurrency: int = int(_env("GLOBAL_CONCURRENCY", "12"))
    per_host_concurrency: int = int(_env("PER_HOST_CONCURRENCY", "4"))
    freshness_hours: int = int(_env("FRESHNESS_HOURS", "24"))


settings = Settings()
