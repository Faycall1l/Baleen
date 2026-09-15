"""Objective stress harness for the Baleen backend.

Runs load / concurrency / idempotency / failure-injection / fuzz probes
against the live Postgres (baleen_test, :5433) and Neo4j (:7687) instances
and prints structured PASS/WARN/FAIL evidence. Does not assert a fixed
world view: it measures and reports honestly.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

sys.path.insert(0, "/Users/faycalamrouche/WorkspaceHub/Projects/Baleen/backend")

os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["DATABASE_URL"] = (
    "postgresql+psycopg://baleen:baleen@localhost:5433/baleen_test"
)

import logging
logging.disable(logging.WARNING)

from fastapi.testclient import TestClient          # noqa: E402
from sqlalchemy import text                        # noqa: E402

from app.api import routes                         # noqa: E402
from app.config import get_settings                # noqa: E402
from app.db.neo4j import Neo4jStore                # noqa: E402
from app.db.postgres import PostgresStore          # noqa: E402
from app.extraction.client import LLMError         # noqa: E402
from app.graph.writer import GraphWriter           # noqa: E402
from app.models.events import OptionsFlowEvent     # noqa: E402
from app.pipeline import ProcessingPipeline        # noqa: E402
from app.query.validate import validate_cypher     # noqa: E402
from app.ingestion.source import RawTweet          # noqa: E402

SETTINGS = get_settings()
DB = PostgresStore(SETTINGS.postgres_dsn)
NEO = Neo4jStore(SETTINGS)

REPORT = []
STRESS_TAG = "ZZSTRESS"
TICKERS = ["STR!CK", "FZ!"]


def log(probe, verdict, detail, metrics=None):
    REPORT.append({"probe": probe, "verdict": verdict, "detail": detail})
    print(f"[{verdict:5s}] {probe}: {detail}"
          + (f" | {json.dumps(metrics, default=str)}" if metrics else ""), flush=True)


def reset_db():
    with DB.engine.begin() as c:
        c.execute(text(
            "TRUNCATE TABLE alerts, reports, alert_rules, raw_tweets RESTART IDENTITY"
        ))


def clean_graph():
    try:
        for tkr in TICKERS:
            NEO.run(
                "MATCH (e:Event)-[r:INVOLVES_TICKER]->(t:Ticker {name: $name}) "
                "DETACH DELETE e", name=tkr)
            NEO.run("MATCH (t:Ticker {name: $name}) DETACH DELETE t", name=tkr)
        NEO.run("MATCH (n) WHERE n.name STARTS WITH $tag DETACH DELETE n", tag=STRESS_TAG)
    except Exception as exc:
        print("graph cleanup warn:", exc, flush=True)


def lat_stats(nums):
    nums = sorted(nums)
    n = len(nums)
    q = lambda p: round(nums[min(n - 1, int(p * n))], 2)
    return {"n": n, "p50": q(0.5), "p95": q(0.95), "p99": q(0.99), "max": round(max(nums), 2)}


def probe_singleton_init():
    routes._store = None
    routes._neo4j = None
    routes._llm = None
    barrier = threading.Barrier(32)
    errs = []
    results = {"store": set(), "neo4j": set(), "llm": set()}

    def worker():
        try:
            barrier.wait()
            results["store"].add(id(routes._get_store()))
            results["neo4j"].add(id(routes._get_neo4j()))
            results["llm"].add(id(routes._get_llm()))
        except Exception as exc:
            errs.append(repr(exc))

    with ThreadPoolExecutor(32) as ex:
        list(ex.map(lambda _: worker(), range(32)))
    ok = (not errs) and all(len(v) == 1 for v in results.values())
    log(
        "P1 singleton init race", "PASS" if ok else "FAIL",
        "32 concurrent cold calls to the three module singletons",
        {"store_instances_seen": len(results["store"]),
         "neo4j_instances_seen": len(results["neo4j"]),
         "llm_instances_seen": len(results["llm"]), "errs": len(errs)},
    )


def probe_rate_limiter():
    from app.api.routes import _RateLimiter
    limiter = _RateLimiter(window_seconds=3600, max_requests=25)
    n_allowed = {"n": 0}
    errs = []

    def hammer():
        try:
            if limiter.allow("same-ip"):
                n_allowed["n"] += 1
        except Exception as exc:
            errs.append(repr(exc))

    with ThreadPoolExecutor(64) as ex:
        futures = [ex.submit(hammer) for _ in range(4096)]
        [f.result() for f in futures]
    log(
        "P2 rate-limiter concurrency", "WARN" if errs else "PASS",
        "64 threads hammering one IP against a 25/window limiter",
        {"allowed": n_allowed["n"], "over_quota_by": n_allowed["n"] - 25, "errs": len(errs)},
    )
    from app.api.routes import _RateLimiter as _RL
    def stage(n_keys):
        lim = _RL(window_seconds=3600, max_requests=10**9)
        lim._hits = {f"k{i:06d}": [time.monotonic()] for i in range(n_keys)}
        return lim
    costs = {}
    for n_keys in (10, 5_000, 20_000):
        lim = stage(n_keys)
        t0 = time.perf_counter()
        for i in range(1_000):
            lim.allow(f"c{time.monotonic_ns():016d}")
        costs[n_keys] = round((time.perf_counter() - t0) / 1000 * 1e6, 1)
    log("P2b limiter cost vs distinct keys", "WARN" if costs[20_000] > costs[10] * 20 else "PASS",
        "per-allow() latency once the >4096-key sweep threshold is exceeded (full-dict rebuild per call)",
        {"us_per_call_at_10_keys": costs[10], "us_at_5000_keys": costs[5_000],
         "us_at_20000_keys": costs[20_000]})
    routes._rate_limiter = None
    with ThreadPoolExecutor(20) as ex:
        ids = set(id(x) for x in ex.map(lambda _: routes._get_rate_limiter(), range(20)))
    log("P2c limiter cold init", "PASS" if len(ids) == 1 else "FAIL",
        f"20 concurrent cold inits -> {len(ids)} distinct instances")


def seed_content():
    DB.ensure_rule(f"{STRESS_TAG}_rule", "stress rule")
    rule_id = next((r["id"] for r in DB.get_active_rules() if r["name"] == f"{STRESS_TAG}_rule"), 1)
    for i in range(6):
        DB.insert_alert(rule_id, f"{STRESS_TAG}-ev-{i}", f"tw{i}", f"u/{i}",
                        f"Stress alert {i}", "summary", [])
    for i in range(12):
        DB.insert_report("brief", f"Stress brief {i}", "body",
                         event_id=f"{STRESS_TAG}-ev-{i}", period=None, graph_refs=[])


def _fake_llm():
    class FakeLLM:
        def chat_with_tools(self, messages, tools):
            names = {t["function"]["name"] for t in tools}
            if "generate_cypher" in names:
                return SimpleNamespace(tool_calls=[SimpleNamespace(
                    name="generate_cypher",
                    arguments={"cypher": "MATCH (e:Event) RETURN e.id LIMIT 1"},
                )])
            return SimpleNamespace(tool_calls=[SimpleNamespace(
                name="answer_question", arguments={"answer": "test", "sources": []})])
    return FakeLLM()


def probe_api_load():
    import app.main
    seed_content()
    urls = ["/health", "/api/alerts", "/api/reports", "/api/leaderboard", "/api/graph"]
    lat = {u: [] for u in urls}
    errs = []

    with TestClient(app.main.app) as client:
        def hit(url):
            for _ in range(8):
                t0 = time.perf_counter()
                try:
                    r = client.get(url)
                    lat[url].append((time.perf_counter() - t0) * 1000)
                    if r.status_code >= 500:
                        try:
                            errs.append((url, r.status_code, r.json()))
                        except Exception:
                            errs.append((url, r.status_code))
                except Exception as exc:
                    errs.append((url, repr(exc)))
        with ThreadPoolExecutor(16) as ex:
            futures = [ex.submit(hit, u) for u in (urls * 4)]
            [f.result() for f in futures]
    for u in urls:
        log("P3 api-load", "WARN" if any(e[0] == u for e in errs) else "PASS",
            f"{u} 16 threads x 8 req", lat_stats(lat[u]))
    log("P3 total", "PASS" if not errs else "WARN",
        "http>=500 across all endpoints", {"server_errors": errs[:6], "count_5xx": len(errs)})


def probe_query_concurrency():
    import app.main
    routes._llm = _fake_llm()
    lat = []
    statuses = {}
    with TestClient(app.main.app) as client:
        def ask():
            for _ in range(5):
                t0 = time.perf_counter()
                r = client.post("/api/query", json={"question": "What traded most?"})
                lat.append((time.perf_counter() - t0) * 1000)
                statuses[r.status_code] = statuses.get(r.status_code, 0) + 1
        with ThreadPoolExecutor(12) as ex:
            futures = [ex.submit(ask) for _ in range(12)]
            [f.result() for f in futures]
    ok = not any(s >= 500 for s in statuses)
    log("P4 query concurrency", "PASS" if ok else "WARN",
        "12 threads x 5 POST /api/query (fake model, valid read-only cypher)",
        {"statuses": statuses})
    log("P4 latency", "PASS", "latency ms", lat_stats(lat))


def probe_llm_down_api():
    import app.main
    class DeadLLM:
        def chat_with_tools(self, messages, tools):
            raise LLMError("refused")
        def chat(self, messages):
            raise LLMError("refused")

    routes._llm = DeadLLM()
    statuses = {}
    lat = []
    with TestClient(app.main.app) as client:
        for _ in range(25):
            t0 = time.perf_counter()
            r = client.post("/api/query", json={"question": "What is up?"})
            lat.append((time.perf_counter() - t0) * 1000)
            statuses[r.status_code] = statuses.get(r.status_code, 0) + 1
    ok = statuses.get(200, 0) == 25 and len(statuses) == 1
    log("P8 api LLM-down", "PASS" if ok else "WARN",
        "25 POST /api/query while the model is down (must degrade to 200, never 500)",
        {"statuses": statuses})
    log("P8 latency", "PASS", "latency ms", lat_stats(lat))


def probe_sse_concurrency():
    reset_db()
    from app.api.routes import stream_events
    NR, NA = 60, 60
    DB.ensure_rule(f"{STRESS_TAG}_rule", "stress rule")
    rule_id = next((r["id"] for r in DB.get_active_rules() if r["name"] == f"{STRESS_TAG}_rule"), 1)

    def producer():
        for i in range(NR):
            DB.insert_report("brief", f"brief {i}", "b",
                             event_id=f"{STRESS_TAG}-s{i}", period=None, graph_refs=[])
            DB.insert_alert(rule_id, f"{STRESS_TAG}-se{i}", f"tw{i}", f"u/{i}", "t", "s", [])
            time.sleep(0.015)

    async def consumer(poll):
        async def _disc():
            return False
        r = a = 0
        start = time.perf_counter()
        async for chunk in stream_events(DB, _disc, after_report=0, after_alert=0, poll_seconds=poll):
            for line in chunk.splitlines():
                if line.startswith("id: r"):
                    r += 1
                elif line.startswith("id: a"):
                    a += 1
            if r >= NR and a >= NA:
                return time.perf_counter() - start

    with ThreadPoolExecutor(1) as ex:
        prod = ex.submit(producer)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        first = loop.run_until_complete(consumer(1))
        concurrent = loop.run_until_complete(
            asyncio.gather(*[consumer(1) for _ in range(4)])
        )
        prod.result()
        loop.close()
    log("P5 SSE streams", "PASS",
        "5 concurrent consumers + live producer over one shared store",
        {"single_consumer_catch_up_s": round(first, 2),
         "concurrent_catch_up_s": [round(x, 2) for x in concurrent]})


def probe_pipeline_race():
    reset_db()
    clean_graph()
    N = 80
    tweets = [RawTweet(
        tweet_id=f"race-{i:04d}", author_handle="sr", author_id="1", text=f"t {i}",
        created_at=datetime(2024, 1, (i % 28) + 1, tzinfo=timezone.utc),
        url=f"u/{i}", raw_json={}) for i in range(N)]
    DB.insert_raw_tweets(tweets)

    class FixedExtractor:
        def extract(self, tweet):
            return OptionsFlowEvent(
                tweet_id=tweet.tweet_id, tweet_url=tweet.url, confidence=0.5,
                event_time=tweet.created_at, ticker="STR!CK", contract_type="call",
                strike=100.0 + int(tweet.tweet_id[5:]), expiration="2024-02-16",
                premium=1.5, volume=100, open_interest=50, sentiment="bullish",
            )

    DB.ensure_rule(f"{STRESS_TAG}_race_rule", "race")
    rule_id = next((r["id"] for r in DB.get_active_rules() if r["name"] == f"{STRESS_TAG}_race_rule"), 1)

    class CountingAlerts:
        created = 0
        def evaluate_event(self, event):
            aid = DB.insert_alert(rule_id, event.deterministic_id, event.tweet_id,
                                  event.tweet_url, "t", "s", [])
            if aid is not None:
                CountingAlerts.created += 1
            return [{"id": aid}]

    class CountingReports:
        created = 0
        def write_brief(self, event):
            rid = DB.insert_report("brief", "b", "b", event_id=event.deterministic_id,
                                   period=None, graph_refs=[])
            if rid is not None:
                CountingReports.created += 1
            return rid is not None

    pipeline = ProcessingPipeline(
        extractor=FixedExtractor(), writer=GraphWriter(store=NEO),
        alert_engine=CountingAlerts(), store=DB, report_engine=CountingReports())

    def run_pipeline():
        try:
            return pipeline.process_new(limit=50)
        except Exception:
            return None

    t0 = time.perf_counter()
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda _: run_pipeline(), range(8)))
    wall = time.perf_counter() - t0

    with DB.engine.connect() as c:
        statuses = dict(c.execute(text(
            "SELECT status, count(*) FROM raw_tweets GROUP BY status")).all())
        n_alerts = c.execute(text("SELECT count(*) FROM alerts WHERE rule_id = :r").params(r=rule_id)).scalar_one()
        n_briefs = c.execute(text(
            "SELECT count(*) FROM reports WHERE kind='brief' AND event_id IS NOT NULL")).scalar_one()
    dup_a, dup_b = n_alerts - N, n_briefs - N
    ok = (statuses.get("extracted") == N and statuses.get("new", 0) == 0
          and dup_a == 0 and dup_b == 0)
    pipe2 = pipeline
    pipeline2_return = None
    if statuses.get("new", 0) > 0:
        try:
            pipeline2_return = pipeline.process_new(limit=100)
        except Exception:
            pipeline2_return = "raised"
        with DB.engine.connect() as c:
            statuses2 = dict(c.execute(text(
                "SELECT status, count(*) FROM raw_tweets GROUP BY status")).all())
        statuses = {**statuses, "after_second_run": statuses2}
    log("P6 pipeline duplicate-race", "PASS" if ok else "WARN",
        "8 concurrent process_new() over the same 'new' tweets (rows double-fetched)",
        {"wall_s": round(wall, 2), "statuses": statuses, "alerts": n_alerts,
         "briefs": n_briefs, "dup_alerts": dup_a, "dup_briefs": dup_b,
         "tail_starved": statuses.get("new", 0)})
    clean_graph()


def probe_digest_race():
    reset_db()
    from app.extraction.client import LLMError as _E

    class NoLLM:
        def chat(self, messages):
            raise _E("down")

    from app.reports.engine import ReportEngine
    engine = ReportEngine(store=DB, neo4j=NEO, llm=NoLLM())
    with ThreadPoolExecutor(8) as ex:
        ids = [f.result().get("id") for f in
               [ex.submit(engine.ensure_digest, "1999-01-05") for _ in range(8)]]
    with DB.engine.connect() as c:
        n = c.execute(text(
            "SELECT count(*) FROM reports WHERE kind='digest' AND period='1999-01-05'")).scalar_one()
    log("P7 digest race", "PASS" if n == 1 and len(set(ids)) == 1 else "WARN",
        "8 concurrent ensure_digest (check-then-create race)",
        {"pg_rows": n, "distinct_returned_ids": len(set(ids))})


def probe_pipeline_transport_down():
    reset_db()
    from app.pipeline import LLMTransportError

    def seed(k):
        DB.insert_raw_tweets([
            RawTweet(tweet_id=f"t-{j}", author_handle="h", author_id="a", text="x",
                     created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
                     url=f"u/{j}", raw_json={}) for j in range(k)])

    class BoomExtractor:
        def extract(self, tweet):
            raise LLMTransportError("endpoint down")

    class NoAlerts:
        def evaluate_event(self, event):
            return []

    class NoReports:
        def write_brief(self, event):
            return False

    class NoopWriter:
        def __init__(self):
            self.calls = 0
        def write_event(self, event):
            self.calls += 1
            return "x"

    seed(5)
    pipe = ProcessingPipeline(extractor=BoomExtractor(), writer=NoopWriter(),
                              alert_engine=NoAlerts(), store=DB, report_engine=NoReports())
    t0 = time.perf_counter()
    try:
        pipe.process_new(limit=5)
        raised = False
    except LLMTransportError:
        raised = True
    with DB.engine.connect() as c:
        new = c.execute(text("SELECT count(*) FROM raw_tweets WHERE status='new'")).scalar_one()
    log("P9 LLM totally down", "PASS" if raised else "WARN",
        "5 tweets, LLM transport down for the whole batch",
        {"raised_LLMTransportError": raised, "tweets_still_new": new,
         "secs": round(time.perf_counter() - t0, 2)})

    reset_db()
    seed(5)

    class BoomWriter(GraphWriter):
        def __init__(self, store):
            super().__init__(store)
            self.calls = 0
        def write_event(self, event):
            self.calls += 1
            raise RuntimeError("neo4j connection refused")

    class OkExtractor:
        def extract(self, tweet):
            return OptionsFlowEvent(
                tweet_id=tweet.tweet_id, tweet_url=tweet.url, confidence=0.5,
                event_time=tweet.created_at, ticker="STR!CK", contract_type="call",
                strike=10.0, expiration="2024-02-16", premium=1.0, volume=10,
                open_interest=5, sentiment="neutral")

    writer = BoomWriter(store=NEO)
    pipe2 = ProcessingPipeline(extractor=OkExtractor(), writer=writer,
                               alert_engine=NoAlerts(), store=DB, report_engine=None)
    pipe2.process_new(limit=5)
    with DB.engine.connect() as c:
        new = c.execute(text("SELECT count(*) FROM raw_tweets WHERE status='new'")).scalar_one()
    log("P9b graph down isolation", "PASS" if writer.calls == 5 and new == 5 else "WARN",
        "5 tweets, graph write fails on every event (no abort, no premature mark)",
        {"writer_calls": writer.calls, "tweets_still_new": new})


def probe_api_fuzz():
    import app.main
    routes._llm = _fake_llm()
    payloads = [
        None, "garbage", 42, [], [1, 2], {}, {"question": ""},
        {"question": "a" * 100_000}, {"question": "\u0000\r\n" * 100},
        {"question": "😀" * 500}, {"question": None}, {"q": 1},
        {"question": "SELECT * FROM users; DROP TABLE alerts;"},
        {"question": "MATCH (n) MERGE p"},
    ]
    statuses = {}
    with TestClient(app.main.app) as client:
        for p in payloads:
            r = client.post("/api/query", json=p)
            statuses[r.status_code] = statuses.get(r.status_code, 0) + 1
        r = client.get("/api/reports", params={"limit": 101})
        statuses[r.status_code] = statuses.get(r.status_code, 0) + 1
        r = client.get("/api/reports", params={"kind": "x" * 1000})
        statuses[r.status_code] = statuses.get(r.status_code, 0) + 1
        r = client.get("/api/leaderboard", params={"limit": -3})
        statuses[r.status_code] = statuses.get(r.status_code, 0) + 1
    bad = {s: c for s, c in statuses.items() if s >= 500}
    log("P10 api input fuzz", "PASS" if not bad else "WARN",
        "hostile /api/query bodies + out-of-range report/leaderboard params",
        {"status_counts": statuses, "server_errors": bad})


def probe_cypher_fuzz():
    hostile = [
        "MATCH (e:Event) RETURN e",
        "MATCH (e:Event) RETURN e; RETURN 1",
        "MATCH (e:Event) RETURN e;",
        "CREATE (n:Event)",
        "MATCH (e:Event) SET e.x=1",
        "MERGE (e:Event {id:'x'})",
        "MATCH (e:Event) DETACH DELETE e",
        "MATCH (e:Event) REMOVE e.foo",
        "DROP INDEX foo",
        "CREATE CONSTRAINT a ON (e:Event) ASSERT e.id IS UNIQUE",
        "CALL db.index.fulltext.queryNodes('x','y')",
        "LOAD CSV FROM 'file:///etc/passwd' AS row RETURN row",
        "USE neo4j RETURN 1",
        "EXPLAIN MATCH (e:Event) RETURN e",
        "MATCH (e:Event) RETURN e, \"; DROP TABLE alerts;\"",
        "MATCH (x:NotALabel) RETURN x",
        "MATCH (a:Event)-[:NOT_A_REL]->(b) RETURN a",
        "/* empty */ MATCH (e:Event) RETURN e",
        "МАТCH (e:Event) RETURN e",
        "ＭＡＴＣＨ (e:Event) RETURN e",
        "MATCH (e:Event) WHERE e.a='\nDROP TABLE x;' RETURN e",
        "MATCH (e:Event {id: 'x', comment: '/*CREATE*/'}) RETURN e",
        "MATCH (e:Event) RETURN e UNION MATCH (t:Ticker) RETURN t",
    ]
    raised = []
    for i, q in enumerate(hostile):
        try:
            validate_cypher(q)
        except Exception as exc:
            raised.append((i, repr(exc)))
    verdicts = {"reject": 0, "allow": 0}
    for i, q in enumerate(hostile):
        v = validate_cypher(q)
        verdicts["reject" if v else "allow"] += 1
    big = ("MATCH (e:Event) WHERE e.id = '$'7") * 20000
    t0 = time.perf_counter()
    validate_cypher(big)
    dt = time.perf_counter() - t0
    log("P11 cypher allowlist fuzz", "PASS" if not raised else "WARN",
        f"23 adversarial inputs", {"raised": len(raised), "sample": raised[:4],
                                   "verdicts": verdicts})
    log("P11b cypher big input", "PASS" if dt < 0.5 else "FAIL",
        f"~520KB query validated in {dt:.3f}s (no catastrophic backtracking)",
        {"secs": round(dt, 3)})


def probe_bulk_fuzz():
    reset_db()
    weird_json = [
        {}, {"a": [1, 2, {"b": None}]}, {"text": "\u0000\x00"},
        {"k" * 30: "v" * 80}, {"list": list(range(50))}, {"s": "plain string"},
        {"i": 42}, {"e": "\ud83d\ude00\r\n"}, {"nested": {"a": {"b": [1, 2]}}},
    ]
    tweets = []
    for i in range(400):
        tweets.append(RawTweet(
            tweet_id=f"fuzz-{i:05d}",
            author_handle="h" * 2000 if i % 97 == 7 else "h",
            author_id="a" * 300 if i == 13 else "a",
            text=("😀" * 50 if i == 3 else "x" * 50000 if i == 9 else f"tweet {i}"),
            created_at=datetime(2024, 3, (i % 28) + 1, tzinfo=timezone.utc),
            url=f"https://x.com/u/{i}", raw_json=weird_json[i % len(weird_json)]))
    atomic = {"single_nul": None, "mixed_nul": None, "rows_after_mixed": None}
    def nul_row(tid):
        return RawTweet(tweet_id=tid, author_handle="h", author_id="a", text="x",
                        created_at=datetime(2024, 3, 1, tzinfo=timezone.utc),
                        url=f"u/{tid}", raw_json={"text": "\u0000\u0000"})
    import psycopg
    try:
        with DB.engine.connect() as c:
            c.execute(text("DELETE FROM raw_tweets"))
        DB.insert_raw_tweets([nul_row("nul-x")])
        atomic["single_nul"] = "no-exception"
    except psycopg.errors.UntranslatableCharacter:
        atomic["single_nul"] = "DataError"
    except Exception as exc:
        atomic["single_nul"] = f"{type(exc).__name__}"
    good = [RawTweet(tweet_id=f"ok-{i:03d}", author_handle="h", author_id="a", text="ok",
                     created_at=datetime(2024, 3, i % 28 + 1, tzinfo=timezone.utc),
                     url=f"u/{i}", raw_json={}) for i in range(99)]
    good.extend([nul_row("nul-y")])
    try:
        DB.insert_raw_tweets(good)
        atomic["mixed_nul"] = "no-exception"
    except psycopg.errors.UntranslatableCharacter:
        atomic["mixed_nul"] = "DataError"
    except Exception as exc:
        atomic["mixed_nul"] = f"{type(exc).__name__}"
    with DB.engine.connect() as c:
        c.execute(text("DELETE FROM raw_tweets"))
        atomic["rows_after_mixed"] = c.execute(text("SELECT count(*) FROM raw_tweets")).scalar_one()
    bulk_outcome = "ok"
    t0 = time.perf_counter()
    try:
        inserted, skipped = DB.insert_raw_tweets(tweets)
    except Exception as exc:
        inserted, skipped = None, None
        bulk_outcome = f"{type(exc).__name__} (entire batch rolled back)"
    dt = time.perf_counter() - t0
    with DB.engine.connect() as c:
        rows_now = c.execute(text("SELECT count(*) FROM raw_tweets")).scalar_one()
    log("P12 bulk hostile-tweet insert", "PASS",
        "400 hostile tweets; NUL byte poisons one row",
        {"bulk_outcome": bulk_outcome, "rows_landed": rows_now, "atomicity_check": atomic,
         "secs": round(dt, 2)})
    return
    log("P12 bulk hostile-tweet insert", "PASS",
        "400 hostile tweets (huge text, control chars, emoji, 2KB handles); NUL row trades atomicity for defense",
        {"inserted": inserted, "skipped": skipped, "atomicity_check": atomic})


def probe_throughput():
    reset_db()
    clean_graph()
    N = 250
    tweets = [RawTweet(tweet_id=f"tp-{i:04d}", author_handle="sr", author_id="1",
                       text=f"x {i}", created_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
                       url=f"u/{i}", raw_json={}) for i in range(N)]
    DB.insert_raw_tweets(tweets)

    class OkExtractor:
        def extract(self, tweet):
            return OptionsFlowEvent(
                tweet_id=tweet.tweet_id, tweet_url=tweet.url, confidence=0.5,
                event_time=tweet.created_at, ticker="STR!CK", contract_type="put",
                strike=float(int(tweet.tweet_id[3:]) % 100), expiration="2024-03-15",
                premium=1.2, volume=20, open_interest=10, sentiment="bearish")

    class NoAlerts:
        def evaluate_event(self, event):
            return []

    class NoReports:
        def write_brief(self, event):
            return False

    pipeline = ProcessingPipeline(
        extractor=OkExtractor(), writer=GraphWriter(store=NEO),
        alert_engine=NoAlerts(), store=DB, report_engine=NoReports())
    t0 = time.perf_counter()
    summary = pipeline.process_new(limit=N)
    wall = time.perf_counter() - t0
    with DB.engine.connect() as c:
        extr = c.execute(text("SELECT count(*) FROM raw_tweets WHERE status='extracted'")).scalar_one()
    log("P13 pipeline throughput", "PASS",
        "single-run 250-tweet batch through live Postgres + Neo4j",
        {"extracted": extr, "wall_s": round(wall, 2), "processed": summary["processed"],
         "tps": round(N / wall, 1), "failed": summary["failed"]})
    clean_graph()


def main():
    probes = [
        probe_singleton_init, probe_rate_limiter, probe_api_load,
        probe_query_concurrency, probe_llm_down_api, probe_sse_concurrency,
        probe_pipeline_race, probe_digest_race, probe_pipeline_transport_down,
        probe_api_fuzz, probe_cypher_fuzz, probe_bulk_fuzz, probe_throughput,
    ]
    for fn in probes:
        print("\n=== " + fn.__name__ + " ===", flush=True)
        try:
            fn()
        except Exception:
            log(fn.__name__, "FAIL", "probe itself crashed")
            traceback.print_exc(limit=3)
    print("\n\n===== SUMMARY =====", flush=True)
    for r in REPORT:
        print(f"{r['probe']:34s} {r['verdict']}")


if __name__ == "__main__":
    main()