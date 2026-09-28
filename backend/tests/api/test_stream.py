"""SSE stream: resume, off-loop polling, error handling, and the route wrapper.

Part of the API suite split out of the former ``tests/test_api.py`` (M1).
"""

from __future__ import annotations

from tests.api.conftest import (
    _StoreBoom,
    _quiet_settings,
)


def test_stream_emits_new_rows_and_resumes_from_cursor(postgres_store):
    """Live SSE feed: catch-up rows first (oldest first), resumable cursors,
    keepalive when quiet. Drives the generator directly (HTTP streaming
    semantics belong to Starlette, not us)."""
    import asyncio
    import uuid

    from app.api.stream import stream_events

    marker = uuid.uuid4().hex[:8]
    postgres_store.insert_report(
        kind="brief", event_id=f"e-stream-{marker}", title="t", body="b"
    )

    async def collect(after_report=0, after_alert=0, max_polls=3):
        polls = {"n": 0}

        async def fake_disconnected():
            polls["n"] += 1
            return polls["n"] > max_polls

        return [
            chunk
            async for chunk in stream_events(
                postgres_store, fake_disconnected, after_report, after_alert,
                poll_seconds=0.01,
            )
        ]

    chunks = asyncio.run(collect())
    data = [c for c in chunks if "\ndata: " in c]
    assert len(data) == 1 and f"e-stream-{marker}" in data[0]
    assert data[0].startswith("id: r") and "\nevent: report\n" in data[0]
    assert any(c == ": keepalive\n\n" for c in chunks)

    # Resume past the row: only keepalives from here on.
    last_id = postgres_store.get_reports_since(0)[-1]["id"]
    resumed = asyncio.run(collect(after_report=last_id))
    assert resumed and all(c == ": keepalive\n\n" for c in resumed)


def test_stream_polls_run_off_the_event_loop():
    import asyncio

    from app.api.stream import stream_events

    class ThreadAwareStore:
        def __init__(self):
            self.saw_running_loop = False

        def get_reports_since(self, after):
            self._record()
            return []

        def get_alerts_since(self, after):
            self._record()
            return []

        def _record(self):
            try:
                asyncio.get_running_loop()
                self.saw_running_loop = True
            except RuntimeError:
                pass

    store = ThreadAwareStore()
    polls = {"n": 0}

    async def disconnected():
        polls["n"] += 1
        return polls["n"] >= 2

    async def collect():
        return [c async for c in stream_events(store, disconnected, 0, 0, poll_seconds=0.01)]

    chunks = asyncio.run(collect())
    assert any(c == ": keepalive\n\n" for c in chunks)
    assert not store.saw_running_loop  # blocking DB calls never ran on the loop


def test_stream_breaks_cleanly_when_store_raises():
    """A backend failure mid-stream terminates the SSE generator without
    throwing — the client just reconnects and resumes from its cursor."""
    import asyncio

    from app.api.stream import stream_events

    store = _StoreBoom()

    async def disconnected():
        return False

    async def collect():
        return [c async for c in stream_events(store, disconnected, 0, 0, poll_seconds=0.01)]

    assert asyncio.run(collect()) == []


def test_stream_emits_alert_and_report_rows():
    import asyncio

    from app.api.stream import stream_events

    class RowStore:
        def __init__(self):
            self.calls = 0

        def get_reports_since(self, after):
            return [{"id": 1, "kind": "brief", "event_id": "e1", "title": "t", "body": "b"}]

        def get_alerts_since(self, after):
            return [{"id": 2, "level": "high", "event_id": "e2", "summary": "s", "kind": "k"}]

    polls = {"n": 0}

    async def disconnected():
        polls["n"] += 1
        return polls["n"] >= 2

    async def collect():
        return [c async for c in stream_events(RowStore(), disconnected, 0, 0, poll_seconds=0.01)]

    chunks = asyncio.run(collect())
    reports = [c for c in chunks if "\nevent: report\n" in c]
    alerts = [c for c in chunks if "\nevent: alert\n" in c]
    assert reports and reports[0].startswith("id: r1")
    assert alerts and alerts[0].startswith("id: a2")


def test_stream_route_wraps_events_in_sse_response(monkeypatch):
    """The /api/stream route wires the events generator into an SSE streaming
    response. Called directly (not over TestClient: infinite streams deadlock
    its transport), with an immediately-disconnected request so the generator
    yields the keepalive chunk once and terminates."""
    import asyncio

    import app.api.routes as routes
    from starlette.responses import StreamingResponse

    from app.api.stream import stream

    _quiet_settings(monkeypatch)

    class _EmptyStore:
        @staticmethod
        def get_reports_since(last_id, limit=50):
            return []

        @staticmethod
        def get_alerts_since(last_id, limit=50):
            return []

    monkeypatch.setattr(routes, "_get_store", lambda: _EmptyStore())

    class _Request:
        def __init__(self):
            self._n = 0

        async def is_disconnected(self):
            self._n += 1
            return self._n > 1  # one quiet poll, then disconnect

    async def run():
        response = await stream(
            request=_Request(), after_report=0, after_alert=0, poll_seconds=1.0
        )
        assert isinstance(response, StreamingResponse)
        assert response.media_type == "text/event-stream"
        chunks = [c async for c in response.body_iterator]
        return response, chunks

    response, chunks = asyncio.run(run())
    assert any("keepalive" in c for c in chunks)
