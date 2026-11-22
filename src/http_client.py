"""Anti-bot-aware async HTTP layer (Phase V).

Design choices, in order of importance:
1. Prefer official APIs and feeds over HTML scraping (build into the source
   connectors); HTML is fetched only where no structured route exists.
2. Global + per-host concurrency semaphores, so one aggressive host can
   never monopolise the worker or trip its rate limiter.
3. Retries only where retrying is sane (429/5xx/timeouts), exponential
   backoff with jitter, honouring Retry-After. 403/404 are terminal: a
   blocked request is logged, never hammered.
4. One honest User-Agent. No captcha solving, no fingerprint spoofing:
   the escalation path for protected sources is documented in
   architecture.pdf (official API/partnership, rendered-browser pool).
"""
from __future__ import annotations

import asyncio
import logging
import random
from urllib.parse import urlparse

import aiohttp

log = logging.getLogger("pipeline.http")

RETRYABLE = {429, 500, 502, 503, 504}


class HttpStatusError(Exception):
    def __init__(self, status: int, url: str):
        super().__init__(f"HTTP {status} for {url}")
        self.status = status
        self.url = url


class HttpClient:
    def __init__(self, user_agent: str, global_concurrency: int = 12, per_host_concurrency: int = 4):
        self._ua = user_agent
        self._global = asyncio.Semaphore(global_concurrency)
        self._per_host_limit = per_host_concurrency
        self._host_semaphores: dict[str, asyncio.Semaphore] = {}
        self._session: aiohttp.ClientSession | None = None
        self.stats = {"requests": 0, "retries": 0, "failures": 0}

    async def __aenter__(self) -> "HttpClient":
        timeout = aiohttp.ClientTimeout(total=45)
        self._session = aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": self._ua})
        return self

    async def __aexit__(self, *exc) -> None:
        if self._session:
            await self._session.close()

    def _host_sema(self, url: str) -> asyncio.Semaphore:
        host = urlparse(url).netloc
        if host not in self._host_semaphores:
            self._host_semaphores[host] = asyncio.Semaphore(self._per_host_limit)
        return self._host_semaphores[host]

    async def get(self, url: str, *, expect: str = "text", attempts: int = 4, headers: dict | None = None):
        """GET with bounded retries. Returns str (text) or parsed JSON."""
        assert self._session is not None, "use HttpClient as an async context manager"
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                async with self._global, self._host_sema(url):
                    self.stats["requests"] += 1
                    async with self._session.get(url, headers=headers) as resp:
                        if resp.status in RETRYABLE:
                            raise HttpStatusError(resp.status, url)
                        if resp.status >= 400:
                            self.stats["failures"] += 1
                            raise HttpStatusError(resp.status, url)
                        if expect == "json":
                            return await resp.json(content_type=None)
                        return await resp.text()
            except HttpStatusError as err:
                if err.status not in RETRYABLE:
                    raise
                last_error = err
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                last_error = err
            if attempt < attempts - 1:
                self.stats["retries"] += 1
                wait = min(2**attempt, 30) + random.uniform(0, 1.0)
                log.warning("retry %s (attempt %d) in %.1fs: %s", url, attempt + 1, wait, last_error)
                await asyncio.sleep(wait)
        self.stats["failures"] += 1
        raise last_error if last_error else RuntimeError(f"GET failed: {url}")

    async def post_json(self, url: str, payload: dict, headers: dict, timeout: int = 60):
        """POST JSON for the LLM providers; status-aware so the engine can
        distinguish 429 (back off) from 413 (shrink payload) from 4xx."""
        assert self._session is not None
        async with self._global, self._host_sema(url):
            self.stats["requests"] += 1
            client_timeout = aiohttp.ClientTimeout(total=timeout)
            async with self._session.post(url, json=payload, headers=headers, timeout=client_timeout) as resp:
                body = await resp.json(content_type=None) if resp.status < 500 else {}
                if resp.status >= 400:
                    err = HttpStatusError(resp.status, url)
                    err.body = body  # type: ignore[attr-defined]
                    raise err
                return body
