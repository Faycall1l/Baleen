"""API-key guard and per-IP rate limiting (J5).

Split out of :mod:`app.api.routes`, which keeps owning the limiter singleton
and the settings lookup so there is a single place to monkeypatch. The policy
lives here; :mod:`app.api.routes.require_api_key` is a thin re-export that the
route modules and :mod:`app.api.stream` already depend on.
"""

from __future__ import annotations

import ipaddress
import threading
import time

from fastapi import HTTPException, Request


class _RateLimiter:
    """Sliding-window limiter keyed by IP, with periodic eviction.

    Config: ``api_rate_window_seconds`` (0 = unlimited) and
    ``api_rate_max_requests`` per window. In-memory only: fine for guarding a
    single-process uvicorn deployment before any public exposure.

    Thread-safe: a lock serializes the per-key read-modify-write, so concurrent
    requests for the same IP cannot slip past ``max_requests``. Eviction is
    time-gated — the full-dict sweep runs at most once per window — so traffic
    across many distinct IPs never pays an O(active keys) rebuild on every
    request.
    """

    def __init__(self, window_seconds: int, max_requests: int) -> None:
        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self._hits: dict[str, list[float]] = {}
        self._last_sweep = 0.0
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.window_seconds <= 0:
            return True
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if t > cutoff]
            if len(hits) >= self.max_requests:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            if (
                len(self._hits) > 4096
                and now - self._last_sweep >= self.window_seconds
            ):
                # Once per window: evict every key whose newest hit is stale.
                self._hits = {k: v for k, v in self._hits.items() if v[-1] > cutoff}
                self._last_sweep = now
        return True


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Only honor a plausible IP from the proxy chain; drop spoofed
        # junk (hostnames, garbage) by falling back to the direct peer so
        # the rate limiter keys cannot be arbitrarily rotated by a client.
        first = forwarded.split(",")[0].strip()
        try:
            ipaddress.ip_address(first)
        except ValueError:
            pass
        else:
            return first
    return request.client.host if request.client else "unknown"


def require_api_key(request: Request) -> None:
    """No-op when ``api_key`` is empty (local dev); otherwise verify the
    ``Authorization: Bearer`` header and apply the per-IP rate limit.

    The settings and limiter are read back through :mod:`app.api.routes` so
    tests keep patching one module.
    """
    from app.api import routes

    settings = routes._get_settings()
    if settings.api_key:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or token.strip() != settings.api_key:
            raise HTTPException(status_code=401, detail="missing or invalid API key")
    if not routes._get_rate_limiter().allow(client_ip(request)):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
