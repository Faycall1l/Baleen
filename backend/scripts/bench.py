"""Trusted performance measurement for the Baleen stack.

Turns the "anything is fast" question into a repeatable, statistics-backed
number, following the measurement methodology used in systems research
(Kalibera & Jones, Laaber et al., duet benchmarking):

- A **replayable workload**: a fixed batch of synthetic tweets is loaded and
  pushed through the real pipeline (extract -> graph -> alerts -> reports)
  every round, with the *same* deterministic ids, so the graph is at steady
  state and the workload is identical round to round.
- **Warmup + sampling**: the first ``--warmup`` rounds are discarded; then
  ``--rounds`` rounds are measured.
- **Per-stage timings**: extraction, graph write, alerts and reports are
  timed per call, plus a Neo4j read probe per round, so you can see where
  time actually goes (as opposed to a single end-to-end number).
- **Statistical reporting**: median, p90/p99, CV and a bootstrap confidence
  interval for the median; never a bare mean. Outliers are shown, not hidden.
- **Ambient conditions are logged**: the host load average is recorded per
  round and on summary, so a run made during k3d/gmem saturation is
  attributable to the host, not the code.
- **Duet A/B mode** (``--duet <sha>``): the current checkout and a baseline
  SHA are measured in alternating rounds on the same host and host DB, and
  we report the per-pair *ratio* (geometric mean + bootstrap CI + sign test).
  This is the "same instances, interleaved order" design from the
  microbenchmarking literature and is the most accurate comparison available
  on a shared, loaded box. ``--aa`` runs the current checkout against itself
  to calibrate false positives first.

Safety: by default the harness targets the *bench/test* services only —
``baleen_test`` on Postgres (5433) and the isolated test Neo4j
(bolt://localhost:7690). Pointing it at the live dev database usually means
you fat-fingered the DSN; ``--allow-dirty-db`` opts out of the guard.

Usage:
    python -m scripts.bench                        # full protocol (defaults)
    python -m scripts.bench --rounds 20 --warmup 3 --batch 8
    python -m scripts.bench --neo4j-uri bolt://localhost:7687 --postgres-dsn .../baleen_test
    python -m scripts.bench --duet <baseline-sha> --duet-pairs 10 --json out.json
    python -m scripts.bench --aa --duet-pairs 6    # A/A false-positive calibration
    python -m scripts.bench --slave                # one round; JSON line (used by --duet)

Requires Postgres baleen_test and a Neo4j instance to be up
(docker compose up -d postgres neo4j-test).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

from app.config import Settings
from app.db.neo4j import Neo4jStore
from app.db.postgres import PostgresStore
from app.extraction.client import LLMResponse, ToolCall
from app.extraction.extractor import EventExtractor
from app.graph.writer import GraphWriter
from app.ingestion.source import RawTweet
from app.pipeline import ProcessingPipeline

logger = logging.getLogger(__name__)

DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7690")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASS = os.getenv("NEO4J_PASSWORD", "baleen_dev_pass")
DEFAULT_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://baleen:baleen@localhost:5433/baleen_test",
)

BOOTSTRAP_ITERS = 2000
BOOTSTRAP_SEED = 20260917


def _loadavg() -> dict[str, float]:
    try:
        one, five, fifteen = os.getloadavg()
        return {"load1": one, "load5": five, "load15": fifteen}
    except OSError:
        return {}


# ---------------------------------------------------------------------------
# Deterministic workload
# ---------------------------------------------------------------------------

#: One template per event type, keyed by a marker embedded in the tweet text
#: and matched by the stub LLM below.
_TEMPLATES = ["BENCH-OPT", "BENCH-CONG", "BENCH-INSIDER"]

_BENCH_TEXTS = {
    "BENCH-OPT": (
        "BIG ALERT: $NVDA 7,500 calls at $130 expiring 06/20 BENCH-OPT are "
        "filling right now. Notional ~$97M, unusual volume vs open interest."
    ),
    "BENCH-CONG": (
        "REPORTED: Rep. Bill Cassidy sold $UNH shares worth $50,001 - $100,000 "
        "on 2026-05-14. Disclosure #D-2026-001. BENCH-CONG"
    ),
    "BENCH-INSIDER": (
        "FORM 4: CEO Jane Doe bought 10,000 shares of BBY at $78.19 on "
        "2026-09-10, direct ownership. BENCH-INSIDER"
    ),
}


def _bench_tweets(k: int, when: datetime) -> list[RawTweet]:
    """K tweets with deterministic, template-round-robin content.

    Ids are stable across rounds (only the occurrence index varies), so every
    round replays the *same* graph MERGE workload — the graph stays roughly
    constant-size and we measure steady-state throughput, not growth.
    """
    tweets = []
    for i in range(k):
        marker = _TEMPLATES[i % len(_TEMPLATES)]
        tweets.append(
            RawTweet(
                tweet_id=f"bench-{i % len(_TEMPLATES)}-{i // len(_TEMPLATES)}",
                author_handle="bench_author",
                author_id="bench-author-1",
                text=_BENCH_TEXTS[marker],
                created_at=when,
                url=f"https://x.com/bench_author/status/bench-{i}",
            )
        )
    return tweets


class DeterministicLLM:
    """Zero-latency stand-in for the real LLM endpoint.

    Extracts a valid, fixed tool call per tweet template and returns a canned
    chat summary for reports. This removes the (external, non-system) LLM
    latency so the benchmark measures *Baleen's* stack: Postgres, Neo4j,
    extraction validation, alerts, reports.
    """

    def chat(self, messages, **kwargs):
        return LLMResponse(content="Stub summary grounded in filings.")

    def chat_with_tools(self, messages, tools, tool_choice="auto"):
        user = next((m["content"] for m in messages if m["role"] == "user"), "")
        if "BENCH-OPT" in user:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        "extract_options_flow",
                        {
                            "ticker": "NVDA",
                            "contract_type": "call",
                            "strike": 130.0,
                            "expiration": "2026-06-20",
                            "premium": 97_000_000.0,
                            "volume": 7500,
                            "open_interest": 120_000,
                            "sentiment": "bullish",
                            "confidence": 0.9,
                        },
                    )
                ]
            )
        if "BENCH-CONG" in user:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        "extract_congress_trade",
                        {
                            "politician_name": "Bill Cassidy",
                            "politician_id": "C000128",
                            "chamber": "senate",
                            "party": "R",
                            "committee_ids": ["H9000"],
                            "ticker": "UNH",
                            "transaction_type": "sell",
                            "amount_range": "$50,001 - $100,000",
                            "transaction_date": "2026-05-14",
                            "confidence": 0.85,
                        },
                    )
                ]
            )
        if "BENCH-INSIDER" in user:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        "extract_insider_trade",
                        {
                            "insider_name": "Jane Doe",
                            "insider_cik": "0001234567",
                            "insider_id": "INS-0000001",
                            "role": "CEO",
                            "ticker": "BBY",
                            "transaction_type": "buy",
                            "shares": 10000.0,
                            "price": 78.19,
                            "transaction_date": "2026-09-10",
                            "confidence": 0.9,
                        },
                    )
                ]
            )
        return LLMResponse(content="")


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class Bench:
    def __init__(self, dsn: str, neo4j_uri: str, user: str, password: str) -> None:
        settings = Settings(
            _env_file=None,
            neo4j_uri=neo4j_uri,
            neo4j_user=user,
            neo4j_password=password,
            neo4j_read_uri=neo4j_uri,
            neo4j_read_user=user,
            neo4j_read_password=password,
        )
        self.settings = settings
        self.store = PostgresStore(dsn)
        self.store.init_schema()
        self.neo4j = Neo4jStore(settings)
        self.neo4j.ensure_indexes()
        self._build_pipeline()

    def _build_pipeline(self) -> None:
        llm = DeterministicLLM()
        extractor = EventExtractor(client=llm)  # type: ignore[arg-type]
        writer = GraphWriter(self.neo4j)
        from app.alerts.engine import AlertEngine
        from app.reports.engine import ReportEngine

        self.pipeline = ProcessingPipeline(
            extractor=extractor,
            writer=writer,
            alert_engine=AlertEngine(store=self.store, neo4j=self.neo4j, llm=llm),  # type: ignore[arg-type]
            store=self.store,
            report_engine=ReportEngine(store=self.store, neo4j=self.neo4j, llm=llm),  # type: ignore[arg-type]
        )

    def close(self) -> None:
        self.store.engine.dispose()
        self.neo4j.close()

    def _clean_bench_tweets(self) -> None:
        from sqlalchemy import text

        with self.store.engine.begin() as conn:
            conn.execute(text("DELETE FROM raw_tweets WHERE tweet_id LIKE 'bench-%'"))

    def read_probe(self) -> float:
        """Latency of one index-backed read of the graph (ms).

        This is the shape of query the graph endpoint fans out; its latency is
        the most sensitive gauge of co-tenancy + graph size on the shared box.
        """
        t0 = time.perf_counter()
        self.neo4j.run_read("MATCH (e:Event) RETURN count(e) AS n")
        return (time.perf_counter() - t0) * 1000.0

    def run_round(self, batch: int, when: datetime) -> dict:
        """One full round; returns per-stage timing buckets + ambient state."""
        self._clean_bench_tweets()
        self.store.insert_raw_tweets(_bench_tweets(batch, when))

        extract_ms: list[float] = []
        write_ms: list[float] = []
        alerts_ms: list[float] = []
        reports_ms: list[float] = []

        ex, wr, al, rp = (
            self.pipeline.extractor,
            self.pipeline.writer,
            self.pipeline.alert_engine,
            self.pipeline.report_engine,
        )

        ex.extract = _timing_wrap(ex.extract, extract_ms)  # type: ignore[method-assign]
        wr.write_event = _timing_wrap(wr.write_event, write_ms)  # type: ignore[method-assign]
        al.evaluate_event = _timing_wrap(al.evaluate_event, alerts_ms)  # type: ignore[method-assign]
        rp.write_brief = _timing_wrap(rp.write_brief, reports_ms)  # type: ignore[method-assign]

        t0 = time.perf_counter()
        summary = self.pipeline.process_new(limit=batch)
        total_s = time.perf_counter() - t0

        probe_ms = self.read_probe()

        return {
            "summary": summary,
            "total_s": total_s,
            "per_tweet_s": total_s / batch,
            "extract_ms": extract_ms,
            "write_ms": write_ms,
            "alerts_ms": alerts_ms,
            "reports_ms": reports_ms,
            "read_probe_ms": [probe_ms],
            "ambient": _loadavg(),
        }


def _timing_wrap(fn, bucket: list[float]):
    def inner(*a, **kw):
        t0 = time.perf_counter()
        try:
            return fn(*a, **kw)
        finally:
            bucket.append((time.perf_counter() - t0) * 1000.0)

    return inner


# ---------------------------------------------------------------------------
# Statistics (stdlib only)
# ---------------------------------------------------------------------------


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def bootstrap_median_ci(values: list[float], iters: int = BOOTSTRAP_ITERS,
                        seed: int = BOOTSTRAP_SEED) -> tuple[float, float]:
    """Percentile bootstrap interval for the median (stdlib)."""
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    medians = sorted(
        statistics.median(values[rng.randrange(n)] for _ in range(n))
        for _ in range(iters)
    )
    return medians[int(0.025 * iters)], medians[int(0.975 * iters)]


def summarize(values: list[float]) -> dict:
    n = len(values)
    if n == 0:
        return {"n": 0}
    mean = statistics.fmean(values)
    stdev = statistics.stdev(values) if n > 1 else 0.0
    sorted_v = sorted(values)
    p90 = sorted_v[max(0, math.ceil(0.90 * n) - 1)]
    p99 = sorted_v[max(0, math.ceil(0.99 * n) - 1)]
    lo, hi = bootstrap_median_ci(values)
    return {
        "n": n,
        "mean_ms": round(mean, 3),
        "median_ms": round(statistics.median(values), 3),
        "stdev_ms": round(stdev, 3),
        "cv_pct": round((stdev / mean * 100.0) if mean else 0.0, 2),
        "min_ms": round(sorted_v[0], 3),
        "p90_ms": round(p90, 3),
        "p99_ms": round(p99, 3),
        "max_ms": round(sorted_v[-1], 3),
        "ci95_median": [round(lo, 3), round(hi, 3)],
    }


def summarize_seconds(values: list[float]) -> dict:
    """Like summarize() but in seconds (for round totals)."""
    s = summarize([v * 1000.0 for v in values])
    return {
        "n": s["n"],
        "mean_ms": s["mean_ms"],
        "median_ms": s["median_ms"],
        "stdev_ms": s["stdev_ms"],
        "cv_pct": s["cv_pct"],
        "min_ms": s["min_ms"],
        "p90_ms": s["p90_ms"],
        "p99_ms": s["p99_ms"],
        "max_ms": s["max_ms"],
        "ci95_median": s["ci95_median"],
        "unit": "s",
    }


def _format_table(rows: list[tuple[str, dict]]) -> str:
    lines = [
        f"{'metric':<16}{'n':>5}{'mean':>11}{'median':>11}{'ci95(median)':>18}"
        f"{'cv%':>7}{'p90':>10}{'p99':>10}{'max':>10}"
    ]
    for name, s in rows:
        ci = (f"[{s['ci95_median'][0]:.0f},{s['ci95_median'][1]:.0f}]"
              if s.get("n") else "-")
        lines.append(
            f"{name:<16}{s['n']:>5}{s['mean_ms']:>11.1f}{s['median_ms']:>11.1f}"
            f"{ci:>18}{s['cv_pct']:>7.1f}{s['p90_ms']:>10.1f}{s['p99_ms']:>10.1f}"
            f"{s['max_ms']:>10.1f}"
        )
    return "\n".join(lines)


def geometric_mean(xs: list[float]) -> float:
    return math.exp(sum(math.log(x) for x in xs) / len(xs))


def mean_and_ci_ratio(ratios: list[float], seed: int = BOOTSTRAP_SEED) -> dict:
    """Geometric-mean ratio + percentile bootstrap CI (duet comparisons)."""
    out = {"geo_mean": round(geometric_mean(ratios), 4), "n": len(ratios)}
    rng = random.Random(seed)
    gms = []
    for _ in range(BOOTSTRAP_ITERS):
        sample = [ratios[rng.randrange(len(ratios))] for _ in range(len(ratios))]
        gms.append(geometric_mean(sample))
    gms.sort()
    out["ci95"] = [round(gms[int(0.025 * BOOTSTRAP_ITERS)], 4),
                   round(gms[int(0.975 * BOOTSTRAP_ITERS)], 4)]
    plus = sum(r > 1.0 for r in ratios)
    minus = sum(r < 1.0 for r in ratios)
    n = plus + minus
    out["excluded_ties"] = len(ratios) - n
    if n == 0:
        out["sign_p"] = 1.0
    else:
        # Exact two-sided sign test (H0: median ratio == 1). For n <= 170 the
        # binomial coefficient is exact/int-exact; beyond that the normal
        # approximation is used. Exact is right at small n, where the normal
        # approximation breaks (and can even exceed 1).
        from math import comb

        k = max(plus, minus)
        if n <= 170:
            tail = sum(comb(n, j) for j in range(k, n + 1)) / (2.0 ** n)
        else:
            z = (abs(plus - n / 2.0) - 0.5) / (math.sqrt(n) / 2.0)
            tail = 1.0 - _normal_cdf(z)
        out["sign_p"] = round(min(1.0, 2.0 * tail), 4)
    return out


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def ensure_test_target(dsn: str, allow_dirty: bool) -> None:
    if not allow_dirty and "baleen_test" not in dsn:
        raise SystemExit(
            f"refusing to benchmark against non-test database {dsn!r} "
            "(expected a DSN containing 'baleen_test'). "
            "Use --allow-dirty-db to override."
        )


def collect_rounds(bench: Bench, *, rounds: int, warmup: int, batch: int) -> list[dict]:
    when = datetime.now(timezone.utc)
    samples: list[dict] = []
    for i in range(warmup + rounds):
        is_warmup = i < warmup
        label = "warmup" if is_warmup else "sample"
        t0 = time.perf_counter()
        result = bench.run_round(batch=batch, when=when)
        load = result["ambient"].get("load1", float("nan"))
        print(f"round {i + 1}/{warmup + rounds} ({label:>6}) "
              f"{result['total_s']:6.2f}s  load1={load:5.2f}  "
              f"probe={result['read_probe_ms'][0]:6.1f}ms", flush=True)
        if not is_warmup:
            samples.append(result)
    return samples


def standard_report(samples: list[dict], batch: int, args) -> None:
    totals = [s["total_s"] for s in samples]
    rows: list[tuple[str, dict]] = [
        ("round_total (s)", summarize_seconds(totals)),
        ("per_tweet (s)", summarize_seconds([s["per_tweet_s"] for s in samples])),
        ("extract", summarize([v for s in samples for v in s["extract_ms"]])),
        ("graph_write", summarize([v for s in samples for v in s["write_ms"]])),
        ("alerts", summarize([v for s in samples for v in s["alerts_ms"]])),
        ("reports", summarize([v for s in samples for v in s["reports_ms"]])),
        ("neo4j_read_probe", summarize([s["read_probe_ms"][0] for s in samples])),
    ]
    print(_format_table(rows))

    amb = _loadavg()
    print(
        f"\nhost load average (1/5/15m): {amb.get('load1', float('nan')):.2f} "
        f"/ {amb.get('load5', float('nan')):.2f} / {amb.get('load15', float('nan')):.2f}"
    )

    if args.json:
        payload = {
            "target": {"postgres_dsn": args.postgres_dsn, "neo4j_uri": args.neo4j_uri},
            "batch": batch,
            "warmup": args.warmup,
            "rounds": args.rounds,
            "ambient": amb,
            "metrics": {name: stat for name, stat in rows},
            "per_round": [
                {
                    "total_s": round(s["total_s"], 3),
                    "per_tweet_s": round(s["per_tweet_s"], 4),
                    "load1": round(s["ambient"].get("load1", float("nan")), 3),
                    "probe_ms": round(s["read_probe_ms"][0], 2),
                }
                for s in samples
            ],
        }
        with open(args.json, "w") as fh:
            json.dump(payload, fh, indent=2)
        print(f"\nwrote {args.json}")


def slave_once(args) -> dict:
    """One measured round, printed as a single JSON line (duet parent consumes)."""
    bench = Bench(args.postgres_dsn, args.neo4j_uri, args.neo4j_user, args.neo4j_password)
    try:
        round_data = bench.run_round(batch=args.batch, when=datetime.now(timezone.utc))
        out = {
            "total_s": round(round_data["total_s"], 4),
            "per_tweet_s": round(round_data["per_tweet_s"], 5),
            "write_median_ms": round(statistics.median(round_data["write_ms"]), 3),
            "read_probe_ms": round(round_data["read_probe_ms"][0], 3),
            "load1": round(round_data["ambient"].get("load1", float("nan")), 3),
            "processed": round_data["summary"]["processed"],
        }
        print(json.dumps(out))
        return out
    finally:
        bench.close()


def _current_backend_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _slave(cwd: str, pythonpath_target: str, args) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = pythonpath_target
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.bench",
         "--slave",
         "--batch", str(args.batch),
         "--neo4j-uri", args.neo4j_uri,
         "--neo4j-user", args.neo4j_user,
         "--neo4j-password", args.neo4j_password,
         "--postgres-dsn", args.postgres_dsn,
         "--allow-dirty-db"],
        cwd=cwd, env=env, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"duet slave failed: {proc.stderr[-2000:]}")
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    if not line:
        raise RuntimeError(f"duet slave produced no JSON: {proc.stdout[-2000:]}")
    return json.loads(line[-1])


def run_duet(args) -> None:
    """Interleave the current checkout and a baseline SHA on the same host."""
    local_cwd = _current_backend_dir()
    local_pp = os.path.join(local_cwd, "app", "__init__.py")

    worktree: str | None = None
    if args.aa:
        remote_backend, remote_pp = local_cwd, local_pp
        label = "A/A (current checkout vs itself)"
    else:
        base = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                       text=True).strip()
        worktree = tempfile.mkdtemp(prefix="baleen-bench-")
        subprocess.check_call(
            ["git", "-C", base, "worktree", "add", "--detach", worktree, args.duet],
            stdout=subprocess.DEVNULL,
        )
        remote_backend = os.path.join(worktree, "backend")
        if not os.path.isdir(remote_backend):
            remote_backend = worktree
        remote_pp = os.path.join(remote_backend, "app", "__init__.py")
        label = f"candidate (current) vs baseline ({args.duet[:10]}...)"

    try:
        pairs: list[tuple[dict, dict]] = []
        for i in range(args.duet_pairs):
            local = _slave(local_cwd, local_pp, args)
            remote = _slave(remote_backend, remote_pp, args)
            pairs.append((local, remote))
            print(
                f"[pair {i + 1}/{args.duet_pairs}] "
                f"local={local['total_s']:.2f}s remote={remote['total_s']:.2f}s "
                f"ratio={local['total_s'] / remote['total_s']:.4f} "
                f"load1={local['load1']:.2f}/{remote['load1']:.2f}",
                flush=True,
            )
    finally:
        if worktree:
            base = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                           text=True).strip()
            subprocess.check_call(["git", "-C", base, "worktree", "remove", "--force",
                                   worktree], stdout=subprocess.DEVNULL)

    series = {
        "round_total_s": [l["total_s"] / r["total_s"] for l, r in pairs if r["total_s"] > 0],
        "per_tweet_s": [l["per_tweet_s"] / r["per_tweet_s"] for l, r in pairs if r["per_tweet_s"] > 0],
        "graph_write_ms": [l["write_median_ms"] / r["write_median_ms"]
                           for l, r in pairs if r["write_median_ms"] > 0],
    }
    print(f"\nDuet result ({label}); per-pair ratio = local / remote, "
          "< 1.0 means the current checkout is faster:\n")
    for name, rs in series.items():
        stat = mean_and_ci_ratio(rs)
        ties = stat["excluded_ties"]
        print(
            f"  {name:<16} geo_mean={stat['geo_mean']:<8.4f} "
            f"ci95={stat['ci95']} sign_test_p={stat['sign_p']} (ties={ties})"
        )

    if args.json:
        payload = {
            "mode": "aa" if args.aa else "duet",
            "baseline_sha": args.duet,
            "label": label,
            "pairs": [
                {
                    "local_s": l["total_s"],
                    "remote_s": r["total_s"],
                    "ratio": l["total_s"] / r["total_s"],
                    "load1": [l["load1"], r["load1"]],
                }
                for l, r in pairs
            ],
            "summary": {
                name: mean_and_ci_ratio(rs) for name, rs in series.items()
            },
        }
        with open(args.json, "w") as fh:
            json.dump(payload, fh, indent=2)
        print(f"\nwrote {args.json}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trusted Baleen performance measurement (see module docstring).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--rounds", type=int, default=10, help="sampled rounds")
    parser.add_argument("--warmup", type=int, default=2, help="warmup rounds (discarded)")
    parser.add_argument("--batch", type=int, default=8, help="tweets per round")
    parser.add_argument("--postgres-dsn", default=DEFAULT_DSN)
    parser.add_argument("--neo4j-uri", default=os.getenv("NEO4J_URI", DEFAULT_NEO4J_URI))
    parser.add_argument("--neo4j-user", default=DEFAULT_NEO4J_USER)
    parser.add_argument("--neo4j-password", default=DEFAULT_NEO4J_PASS)
    parser.add_argument("--allow-dirty-db", action="store_true",
                        help="allow a non-baleen_test Postgres DSN")
    parser.add_argument("--json", default=None, help="write summary JSON to this path")
    parser.add_argument("--duet", metavar="SHA", default=None,
                        help="compare the current checkout vs a baseline SHA (interleaved)")
    parser.add_argument("--duet-pairs", type=int, default=6)
    parser.add_argument("--aa", action="store_true",
                        help="A/A self-check: current checkout vs itself (false-positive calibration)")
    parser.add_argument("--slave", action="store_true",
                        help="one round; print a JSON line (used by --duet)")
    args = parser.parse_args()

    if args.slave:
        slave_once(args)
        return

    ensure_test_target(args.postgres_dsn, args.allow_dirty_db)

    # The pipeline's own INFO logging drowns the per-round progress line.
    logging.getLogger("app").setLevel(logging.ERROR)

    if args.duet or args.aa:
        run_duet(args)
        return

    print(f"target: postgres={args.postgres_dsn} neo4j={args.neo4j_uri} "
          f"(batch={args.batch}, warmup={args.warmup}, rounds={args.rounds})")
    bench = Bench(args.postgres_dsn, args.neo4j_uri, args.neo4j_user, args.neo4j_password)
    try:
        samples = collect_rounds(bench, rounds=args.rounds, warmup=args.warmup, batch=args.batch)
    finally:
        bench.close()

    standard_report(samples, args.batch, args)


if __name__ == "__main__":
    main()