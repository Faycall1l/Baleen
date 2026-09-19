# Demo

Baleen watches Congress trade stocks — and flags when they bet on their own
committees. Congressional trades, insider filings, and unusual options flow go
into a graph you can ask questions about. All live, no API keys for data.

## Run it

```bash
docker compose up
```

Backend ticks on its own every 5 min. UI on :5173, API on :8000.

## Look at

Graph first. Click anything — every node links back to its source filing or
quote page.

Then ask it stuff (or click an example under the bar):

- Which tickers did congressional traders buy most recently?
- What sectors does the House Financial Services Committee oversee, and which
  of those sectors have tickers in the graph?
- Show unusual options flow in the last 24 hours by sector.

It shows the Cypher it ran under every answer. If there is no data it says
so — it does not make things up.

Alerts fire on the watch rules: oversight-sector trades, $1M+ insider buys,
unusual options sweeps. Reports are published for every event plus a daily
digest, same thing at /api/reports.

The leaderboard ranks every disclosed trade by return since filing, with the
official filing linked. Ask "who in Congress is actually beating the market"
and point at it.

The signals panel reads the graph for pressure: congress buying momentum per
ticker, insider cluster buys, bullish vs bearish options volume, trades
already beating the market, and politicians trading sectors nobody on their
committees oversees. All computed live at /api/signals, no model involved.

## Prove it is live

Watch fresh filings come in on the next tick, or tail the backend log and
watch it do the whole thing by itself.
