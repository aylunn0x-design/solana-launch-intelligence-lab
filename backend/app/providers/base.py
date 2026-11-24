"""Provider abstraction: rate limiting, retries, and a common response envelope.

Why this layer exists
---------------------
Free market-data APIs enforce undocumented and changeable rate limits. During
development, GeckoTerminal returned HTTP 429 after roughly six requests in a
minute, and the Solana public RPC returned 429 on the *first* call to an
expensive method. Any ingestion code written against these APIs must therefore
treat throttling as the normal case, not an error path.

`RateLimitedClient` provides:
  * a token-bucket limiter (requests per minute, configurable per provider),
  * exponential backoff with jitter on 429/5xx, honouring `Retry-After`,
  * adaptive slowdown: repeated 429s shrink the effective rate for the process,
  * per-request accounting so an ingestion job can report how many HTTP calls it
    made and how often it was throttled.

Errors are never swallowed: a request that ultimately fails raises
`ProviderError`, and the caller records it against the job.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """A provider request failed in a way the caller must handle explicitly."""

    def __init__(self, message: str, *, status_code: int | None = None, endpoint: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.endpoint = endpoint


class RateLimitExceeded(ProviderError):
    """Retries were exhausted while being throttled."""


@dataclass
class RequestStats:
    """Accounting for one ingestion run."""

    requests_made: int = 0
    rate_limit_hits: int = 0
    retries: int = 0
    failures: int = 0

    def merge(self, other: "RequestStats") -> None:
        self.requests_made += other.requests_made
        self.rate_limit_hits += other.rate_limit_hits
        self.retries += other.retries
        self.failures += other.failures


@dataclass
class ProviderResponse:
    """A successful response plus the metadata needed for provenance."""

    endpoint: str
    params: dict[str, Any]
    status_code: int
    payload: Any
    fetched_at: float = field(default_factory=time.time)


class TokenBucket:
    """Simple async token bucket: at most `rate_per_min` requests per minute.

    Deliberately simple and in-process. A distributed limiter would be the right
    answer for multiple workers, but this project runs ingestion as a single
    process, and adding Redis here would be unjustified complexity.
    """

    #: consecutive throttle-free requests before the rate is allowed to recover
    RECOVERY_AFTER_CLEAN_REQUESTS = 25

    def __init__(self, rate_per_min: int):
        self.configured_rate_per_min = max(1, rate_per_min)
        self.rate_per_min = self.configured_rate_per_min
        self._interval = 60.0 / self.rate_per_min
        self._next_allowed = 0.0
        self._lock = asyncio.Lock()
        self._clean_requests = 0

    def _set_rate(self, rate: int) -> None:
        self.rate_per_min = max(1, rate)
        self._interval = 60.0 / self.rate_per_min

    def shrink(self, factor: float = 0.5, floor_per_min: int = 5) -> None:
        """Slow down after repeated throttling."""
        new_rate = max(floor_per_min, int(self.rate_per_min * factor))
        if new_rate < self.rate_per_min:
            logger.warning(
                "Adaptive rate limit: %d -> %d req/min after repeated 429s",
                self.rate_per_min,
                new_rate,
            )
            self._set_rate(new_rate)
        self._clean_requests = 0

    def note_clean_request(self) -> None:
        """Record a request that was not throttled, and recover gradually.

        Without this, a single burst of 429s early in a long backfill would
        permanently cripple throughput for the rest of the run. Recovery is
        capped at the configured rate and only creeps upward, so it cannot
        stampede back into the limit.
        """
        if self.rate_per_min >= self.configured_rate_per_min:
            self._clean_requests = 0
            return
        self._clean_requests += 1
        if self._clean_requests >= self.RECOVERY_AFTER_CLEAN_REQUESTS:
            recovered = min(self.configured_rate_per_min, max(self.rate_per_min + 1,
                                                              int(self.rate_per_min * 1.5)))
            if recovered > self.rate_per_min:
                logger.info(
                    "Rate limit recovering: %d -> %d req/min after %d clean requests",
                    self.rate_per_min, recovered, self._clean_requests,
                )
                self._set_rate(recovered)
            self._clean_requests = 0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_allowed = max(now, self._next_allowed) + self._interval


class RateLimitedClient:
    """An httpx client wrapper with limiting, retries and accounting."""

    #: how many 429s before we permanently reduce our own rate
    ADAPTIVE_THRESHOLD = 3

    def __init__(
        self,
        base_url: str,
        *,
        rate_per_min: int,
        timeout: float = 25.0,
        max_retries: int = 4,
        headers: dict[str, str] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.bucket = TokenBucket(rate_per_min)
        self.max_retries = max_retries
        self.stats = RequestStats()
        self._consecutive_429 = 0
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers=headers or {},
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "RateLimitedClient":
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.aclose()

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> float | None:
        raw = response.headers.get("retry-after")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    async def get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        method: str = "GET",
        json_body: dict | None = None,
    ) -> ProviderResponse:
        """Perform a request, retrying throttles and transient failures.

        Raises ProviderError on non-retryable errors and RateLimitExceeded when
        retries are exhausted. Never returns a fabricated result.
        """
        params = params or {}
        last_error: str = "no attempt made"

        for attempt in range(self.max_retries + 1):
            await self.bucket.acquire()
            try:
                self.stats.requests_made += 1
                if method == "POST":
                    response = await self._client.post(path, json=json_body)
                else:
                    response = await self._client.get(path, params=params)
            except httpx.HTTPError as exc:
                last_error = f"transport error: {type(exc).__name__}: {exc}"
                logger.warning("%s %s attempt %d: %s", method, path, attempt + 1, last_error)
                self.stats.retries += 1
                await self._sleep_backoff(attempt)
                continue

            if response.status_code == 429:
                self.stats.rate_limit_hits += 1
                self._consecutive_429 += 1
                if self._consecutive_429 >= self.ADAPTIVE_THRESHOLD:
                    self.bucket.shrink()
                    self._consecutive_429 = 0
                retry_after = self._retry_after_seconds(response)
                last_error = "HTTP 429 rate limited"
                logger.warning(
                    "Rate limited on %s (attempt %d/%d), retry_after=%s",
                    path,
                    attempt + 1,
                    self.max_retries + 1,
                    retry_after,
                )
                self.stats.retries += 1
                await self._sleep_backoff(attempt, floor=retry_after)
                continue

            if 500 <= response.status_code < 600:
                last_error = f"HTTP {response.status_code} server error"
                self.stats.retries += 1
                await self._sleep_backoff(attempt)
                continue

            if response.status_code >= 400:
                # Client errors are not retryable; surface them immediately.
                self.stats.failures += 1
                raise ProviderError(
                    f"HTTP {response.status_code} for {path}: {response.text[:200]}",
                    status_code=response.status_code,
                    endpoint=path,
                )

            self._consecutive_429 = 0
            self.bucket.note_clean_request()
            try:
                payload = response.json()
            except ValueError as exc:
                self.stats.failures += 1
                raise ProviderError(
                    f"Response from {path} was not valid JSON: {exc}", endpoint=path
                ) from exc

            return ProviderResponse(
                endpoint=path,
                params=params,
                status_code=response.status_code,
                payload=payload,
            )

        self.stats.failures += 1
        raise RateLimitExceeded(
            f"Gave up on {path} after {self.max_retries + 1} attempts: {last_error}",
            endpoint=path,
        )

    async def _sleep_backoff(self, attempt: int, floor: float | None = None) -> None:
        """Exponential backoff with jitter, respecting a server-provided floor."""
        delay = min(60.0, 2.0 ** attempt)
        delay += random.uniform(0, 0.5 * delay)  # jitter: avoid lockstep retries
        if floor is not None:
            delay = max(delay, floor)
        await asyncio.sleep(delay)
