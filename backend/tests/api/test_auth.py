"""Auth and rate limiting: sliding window, API key guard, per-IP query limits, client IP resolution.

Part of the API suite split out of the former ``tests/test_api.py`` (M1).
"""

from __future__ import annotations

import pytest

from tests.api.conftest import _client, _services_available

def test_rate_limiter_sliding_window():
    from app.api.routes import _RateLimiter

    limiter = _RateLimiter(window_seconds=60, max_requests=3)
    key = "1.2.3.4"
    assert limiter.allow(key)
    assert limiter.allow(key)
    assert limiter.allow(key)
    assert not limiter.allow(key)  # window exhausted
    assert limiter.allow("other-ip")  # different key unaffected
    assert _RateLimiter(0, 3).allow(key)  # window 0 = unlimited


def test_api_key_guard_blocks_unauthenticated(monkeypatch):
    import app.api.routes as routes
    from app.config import get_settings

    real_settings = get_settings()

    def secure_settings():
        return real_settings.model_copy(update={"api_key": "secret-key"})

    monkeypatch.setattr(routes, "get_settings", secure_settings)

    with _client() as c:
        assert c.get("/api/graph").status_code == 401
        assert (
            c.get("/api/graph", headers={"Authorization": "Bearer wrong"}).status_code == 401
        )


def test_api_key_guard_accepts_valid_key(monkeypatch):
    if not _services_available():
        pytest.skip("postgres/neo4j not available")

    import app.api.routes as routes
    from app.config import get_settings

    real_settings = get_settings()

    def secure_settings():
        return real_settings.model_copy(update={"api_key": "secret-key"})

    monkeypatch.setattr(routes, "get_settings", secure_settings)

    with _client() as c:
        res = c.get("/api/graph", headers={"Authorization": "Bearer secret-key"})
        assert res.status_code == 200


def test_query_rate_limited_per_ip(monkeypatch):
    if not _services_available():
        pytest.skip("postgres/neo4j not available")

    import app.api.routes as routes
    from app.config import get_settings

    real_settings = get_settings()

    def throttled_settings():
        return real_settings.model_copy(
            update={"api_rate_window_seconds": 3600, "api_rate_max_requests": 2}
        )

    monkeypatch.setattr(routes, "get_settings", throttled_settings)
    monkeypatch.setattr(routes, "_rate_limiter", None)

    with _client() as c:
        assert c.get("/api/graph").status_code == 200
        assert c.get("/api/graph").status_code == 200
        assert c.get("/api/graph").status_code == 429


def test_client_ip_honors_forwarded_header():
    from app.api.auth import client_ip
    from fastapi import Request
    from starlette.datastructures import Headers

    req = Request(
        {
            "type": "http",
            "headers": Headers({"x-forwarded-for": "203.0.113.7, 10.0.0.1"}).raw,
            "client": ("10.0.0.1", 12345),
        }
    )

    assert client_ip(req) == "203.0.113.7"


def test_client_ip_falls_back_to_peer_for_spoofed_forwarded_header():
    """Garbage in X-Forwarded-For must not become a rate-limit key: a client
    could otherwise rotate keys to defeat the limiter and its eviction sweep."""
    from app.api.auth import client_ip
    from fastapi import Request
    from starlette.datastructures import Headers

    def req(xff: str):
        return Request(
            {
                "type": "http",
                "headers": Headers({"x-forwarded-for": xff}).raw,
                "client": ("203.0.113.9", 12345),
            }
        )

    for spoofed in ["not-an-ip", "evil.example", "203.0.113.7 garbage", ":::", ""]:
        assert client_ip(req(spoofed)) == "203.0.113.9"
    # a valid IPv6 address is accepted as a bonafide proxy-derived peer
    assert client_ip(req("2001:db8::1, 10.0.0.1")) == "2001:db8::1"


def test_rate_limiter_prunes_stale_keys_on_sweep(monkeypatch):
    """The sweep only runs past 4096 total keys, but then it evicts every
    entry older than the window — so a stale flood can never pin the map."""
    import app.api.auth as auth
    import app.api.routes as routes

    clock = {"now": 0.0}

    def fake_now():
        return clock["now"]

    # The limiter reads time via app.api.auth; patching routes.time only
    # worked because both names bound the same stdlib module.
    monkeypatch.setattr(auth.time, "monotonic", fake_now)
    monkeypatch.setattr(routes, "_rate_limiter", None)

    limiter = routes._RateLimiter(window_seconds=60, max_requests=2)
    for i in range(5000):
        limiter.allow(f"stale-{i}")
    assert len(limiter._hits) == 5000  # stays resident inside the window

    clock["now"] += 120.0  # every entry is now older than the window
    for _ in range(3):  # hammer one hot key -> 3rd call is a denial -> sweep
        limiter.allow("hot")

    assert set(limiter._hits) == {"hot"}  # stale-* entries all evicted
