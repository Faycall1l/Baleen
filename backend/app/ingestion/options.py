"""Real unusual-options-flow radar: free delayed CBOE chains.

Pulls delayed option chains from CBOE's public delayed-quotes endpoint (no key,
~15-minute delay) for a liquid watchlist and flags contracts whose day volume
dwarfs open interest — the textbook "unusual flow" signature. Each anomaly
becomes a :class:`RawTweet` in the familiar flow-report style, URL'd at the
ticker's quote page.

Politeness is load-bearing: one chain is ~1.5MB, so responses are TTL-cached,
tickers are fetched sequentially with a delay, and any 429/5xx stops the sweep
(the scheduler's backoff then spaces out retries). Re-polls re-emit the same
intraday-stable ids and downstream dedup drops them.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from app.graph.seed_data import TICKER_SECTORS
from app.ingestion.source import FetchResult, RawTweet, TweetSource

logger = logging.getLogger(__name__)

SOURCE_KIND = "options_radar"

CHAIN_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{ticker}.json"
QUOTE_URL = "https://finance.yahoo.com/quote/{ticker}"

#: Liquid names with active option markets (subset of the reference sectors).
WATCHLIST: list[str] = [
    "NVDA", "TSLA", "AAPL", "AMD", "MSFT",
    "META", "GOOGL", "JPM", "XOM", "LLY",
]

#: Unusual = day volume at least this multiple of open interest, with at least
#: this much absolute volume (filters penny-contract noise).
MIN_VOLUME_RATIO = 2.0
MIN_VOLUME = 500

#: Best anomalies per ticker per poll (by volume). Bounds LLM extraction cost.
MAX_PER_TICKER = 3

#: Chains refresh continuously but quotes lag ~15m; re-fetch at most this often.
CHAIN_TTL_SECONDS = 900

#: A failing chain is not re-downloaded for this long (avoids hammering a
#: dead/delisted ticker every poll while it sits failing).
FAIL_TTL_SECONDS = 300

#: Courtesy gap between ticker fetches.
FETCH_GAP_SECONDS = 1.0

_UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}

_OCC_RE = re.compile(r"^(.+?)(\d{6})([CP])(\d{8})$")


def parse_occ(symbol: str) -> tuple[str, str, str, float] | None:
    """Split an OCC option symbol into (root, expiry YYYY-MM-DD, C|P, strike)."""
    match = _OCC_RE.match((symbol or "").strip())
    if not match:
        return None
    root, ymd, kind, strike = match.groups()
    try:
        expiry = f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:]}"
        datetime.strptime(expiry, "%Y-%m-%d")
        return root.strip(), expiry, kind, int(strike) / 1000.0
    except ValueError:
        return None


def _anomalies(contracts: list[dict], day: str) -> list[dict]:
    scored = []
    for contract in contracts:
        volume = contract.get("volume") or 0
        interest = contract.get("open_interest") or 0
        if volume < MIN_VOLUME:
            continue
        ratio = volume / max(interest, 1)
        if ratio < MIN_VOLUME_RATIO:
            continue
        parsed = parse_occ(str(contract.get("option") or ""))
        if parsed is None:
            continue
        root, expiry, kind, strike = parsed
        scored.append(
            {
                "ticker": root,
                "expiry": expiry,
                "kind": kind,
                "strike": strike,
                "volume": int(volume),
                "open_interest": int(interest),
                "ratio": round(ratio, 1),
                "last": contract.get("last_trade_price"),
                "last_time": contract.get("last_trade_time"),
                "day": day,
            }
        )
    scored.sort(key=lambda a: a["volume"], reverse=True)
    return scored[:MAX_PER_TICKER]


def _record(anomaly: dict, now: datetime) -> RawTweet:
    ticker = anomaly["ticker"]
    side = "calls" if anomaly["kind"] == "C" else "puts"
    strike = f"{anomaly['strike']:g}"
    last = anomaly.get("last")
    last_txt = f" Last ${last}." if last else ""
    text = (
        f"UNUSUAL FLOW (delayed ~15m): ${ticker} {anomaly['volume']:,} {side} "
        f"${strike} strike expiring {anomaly['expiry']} — volume "
        f"{anomaly['volume']:,} vs open interest {anomaly['open_interest']:,} "
        f"({anomaly['ratio']}x).{last_txt} Source: CBOE delayed quotes."
    )
    meta = dict(anomaly)
    meta["source_kind"] = SOURCE_KIND
    return RawTweet(
        tweet_id=(
            f"opt-{ticker}-{anomaly['expiry']}-{strike}-"
            f"{anomaly['kind']}-{anomaly['day']}"
        ),
        author_handle="options_radar",
        author_id="options_radar",
        text=text,
        created_at=now,
        url=QUOTE_URL.format(ticker=ticker),
        raw_json=meta,
    )


class OptionsChainSource(TweetSource):
    """Sweeps the watchlist for unusual-volume contracts."""

    def __init__(
        self,
        watchlist: list[str] | None = None,
        timeout_seconds: float = 20.0,
        fetcher: Callable[[str], dict] | None = None,
    ) -> None:
        self.watchlist = [t.upper() for t in (watchlist or WATCHLIST)]
        for ticker in self.watchlist:
            if ticker not in TICKER_SECTORS:
                logger.warning("watchlist ticker %s has no sector mapping", ticker)
        self.timeout_seconds = timeout_seconds
        self._fetcher = fetcher or self._fetch_chain
        self._cache: dict[str, tuple[float, dict]] = {}
        # issue #13: recent failures are remembered briefly so a dead chain
        # isn't re-downloaded every poll while it sits failing.
        self._fail_until: dict[str, float] = {}

    # ------------------------------------------------------------------
    # TweetSource interface
    # ------------------------------------------------------------------

    def fetch_recent(self, handles: list[str], since_seconds: int) -> FetchResult:
        """``handles``/``since_seconds`` don't apply to a chain sweep; every
        poll re-emits today's anomalies under stable ids (dedup downstream)."""
        now = datetime.now(timezone.utc)
        day = now.date().isoformat()
        tweets: list[RawTweet] = []
        errors: list[str] = []
        for ticker in self.watchlist:
            try:
                payload = self._cached_chain(ticker)
            except Exception as exc:
                # issue #13: one failing ticker must not starve the rest of
                # the watchlist — log the error and keep sweeping.
                errors.append(f"{ticker} chain fetch failed: {exc}")
                continue
            contracts = ((payload.get("data") or {}).get("options") or [])
            for anomaly in _anomalies(contracts, day):
                tweets.append(_record(anomaly, now))
            time.sleep(FETCH_GAP_SECONDS)
        logger.info("options radar: %d anomalie(s) across %d ticker(s)", len(tweets), len(self.watchlist))
        return FetchResult(tweets=tweets, errors=errors)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _cached_chain(self, ticker: str) -> dict:
        hit = self._cache.get(ticker)
        if hit is not None and time.monotonic() - hit[0] < CHAIN_TTL_SECONDS:
            return hit[1]
        until = self._fail_until.get(ticker, 0.0)
        if time.monotonic() < until:
            raise ValueError("chain recently failed; holding off")
        try:
            payload = self._fetcher(ticker)
            if not isinstance(payload, dict):
                raise ValueError(f"chain for {ticker} returned {type(payload).__name__}")
        except Exception:
            self._fail_until[ticker] = time.monotonic() + FAIL_TTL_SECONDS
            raise
        self._cache[ticker] = (time.monotonic(), payload)
        self._fail_until.pop(ticker, None)
        return payload

    def _fetch_chain(self, ticker: str) -> dict:
        response = httpx.get(
            CHAIN_URL.format(ticker=ticker), headers=_UA, timeout=self.timeout_seconds
        )
        response.raise_for_status()
        return response.json()
