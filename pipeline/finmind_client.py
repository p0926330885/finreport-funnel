"""
FinMind API client with rate limit + retry + optional mock mode.

Rate limit: default 500 req/hr (SPEC §5.1, buffered under 600/hr free tier cap).
Mock mode: for local test / bash_tool demo, set env FINMIND_MOCK=1.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from . import config

log = logging.getLogger(__name__)


class RateLimiter:
    """Simple token-bucket-ish limiter: sleeps to enforce min interval between calls."""

    def __init__(self, interval_seconds: float):
        self.interval = interval_seconds
        self._last_call: float = 0.0

    def wait(self) -> None:
        elapsed = time.time() - self._last_call
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last_call = time.time()


@dataclass
class FinMindClient:
    """Thin wrapper on FinMind /data endpoint. Use `fetch(dataset, **params)`."""

    token: str
    base_url: str = config.FINMIND_API_BASE
    rate_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(config.RATE_LIMIT_INTERVAL_SECONDS))
    mock: bool = False

    def fetch(self, dataset: str, **params: Any) -> list[dict]:
        """Return `data` array from FinMind response, or [] on empty."""
        if self.mock:
            return self._mock_fetch(dataset, **params)

        self.rate_limiter.wait()
        url = f"{self.base_url}/data"
        query = {"dataset": dataset, "token": self.token, **params}

        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                resp = requests.get(url, params=query, timeout=30)
                if resp.status_code == 402:
                    # rate limit exceeded
                    log.warning("FinMind rate limit hit, backing off (%s)", dataset)
                    time.sleep(config.RETRY_BACKOFF_SECONDS * attempt * 4)
                    continue
                resp.raise_for_status()
                payload = resp.json()
                if payload.get("status") != 200:
                    log.warning("FinMind non-200 payload: %s", payload.get("msg"))
                    return []
                return payload.get("data", [])
            except requests.RequestException as exc:
                log.warning("FinMind request failed (attempt %d/%d): %s",
                            attempt, config.MAX_RETRIES, exc)
                time.sleep(config.RETRY_BACKOFF_SECONDS * attempt)

        log.error("FinMind fetch failed after %d attempts: %s", config.MAX_RETRIES, dataset)
        return []

    # ------------------------------------------------------------
    # Mock mode (local demo)
    # ------------------------------------------------------------
    def _mock_fetch(self, dataset: str, **params: Any) -> list[dict]:
        """Return synthetic data for demo / test.

        Loaded from cache/mock/{dataset}/{stock_id}.json when available,
        else generates on the fly via mock_data module.
        """
        from . import mock_data
        return mock_data.generate(dataset, **params)


def load_client() -> FinMindClient:
    """Factory: reads FINMIND_TOKEN + FINMIND_MOCK from env."""
    mock = os.environ.get("FINMIND_MOCK") == "1"
    token = os.environ.get("FINMIND_TOKEN", "")
    if not mock and not token:
        raise SystemExit(
            "FINMIND_TOKEN env var not set. "
            "Set it (or FINMIND_MOCK=1 for local test)."
        )
    return FinMindClient(token=token, mock=mock)
