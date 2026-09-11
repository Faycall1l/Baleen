# demo

baleen watches congressional trades and weird options flow, puts it in a
graph, and lets you ask questions about it. all live, no api keys for data.

## run it

```bash
docker compose up -d postgres neo4j
cd backend && python -m scripts.ingest_once --real --seed-ref
```

backend keeps the loop going every 5 min by itself. ui on :5173, api on :8000.

## look at

graph first. ~600 nodes. click anything, every node links back to its source
filing or quote page.

then ask it stuff (or just click one of the example questions under the bar):

- which tickers did congressional traders buy most recently?
- what sectors does the house financial services committee oversee, and which
  of those sectors have tickers in the graph?
- show unusual options flow in the last 24 hours by sector.

it shows the cypher it ran under every answer. if theres no data it says so,
doesnt make shit up.

alerts fire when a politician trades in a sector their own committee
oversees. reports get published for every event plus a daily digest, same
thing at /api/reports.

leaderboard ranks every disclosed trade by return since filing, with the
official filing linked. ask "who in congress is actually beating the market"
and point at it.

## prove its live

run `ingest_once --real` again and watch fresh filings come in. or tail the
backend log and watch the tick do the whole thing by itself.
