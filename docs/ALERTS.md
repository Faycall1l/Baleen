# Baleen — Alert Engine

Lightweight rule evaluation over newly written graph events. In MVP scope there
is exactly **one** rule; the design leaves room for more without shipping them.

## Flow

```
new Event written to Neo4j
        │
        ▼
alerts/engine.py picks up the event
        │
        ▼
evaluates each active row in alert_rules
        │
        ├── no trigger ──► done (logged, no alert)
        │
        └── trigger ──► LLM drafts 2–3 sentence factual summary
                        │
                        ▼
              stored in alerts table
                        │
                        ▼
              served to the Alerts Panel via /api/alerts
```

The engine reuses the same LLM client wrapper as extraction
(`app/extraction/client.py`) — no separate provider wiring.

## The one MVP rule: `politician_trades_in_oversight_sector`

**When it fires.** A `CongressTradeEvent` references a politician `P` who trades
a ticker `T`, and `T`'s sector is overseen by at least one committee `P` is a
member of.

**Graph path.**
```
(Event)-[:INVOLVES_POLITICIAN]->(Politician P)
(Event)-[:INVOLVES_TICKER]->(Ticker T)-[:IN_SECTOR]->(Sector S)
(Politician P)-[:MEMBER_OF]->(Committee C)-[:OVERSEES]->(Sector S)   // same S
```

**Alert output.** Title: `"<Politician> traded <TICKER> in <Sector>, a sector
their committee oversees"`. Body: an LLM-drafted 2–3 sentence summary grounded
in the graph facts (politician, ticker, transaction type, amount, sector,
committee, date), always citing the source tweet URL.

## Rule configuration

Rules live in the `alert_rules` table. The MVP rule is seeded by
`backend/scripts/seed_reference.py` (or `scripts/seed_alerts.py`):

| name | description | config |
| --- | --- | --- |
| `politician_trades_in_oversight_sector` | Alerts when a politician trades a ticker whose sector is overseen by one of their committees | `{}` |

Disable a rule with `UPDATE alert_rules SET active = false WHERE name = ...`.
The engine logs every evaluation (rule, event, triggered yes/no).

## Why it's grounded

The alert is only ever produced when the graph path above actually exists for a
newly written event. The LLM summarizes existing facts — it never invents a
trade, a ticker, or a committee. If the path doesn't exist, no alert fires.

Insider-trade events can never trigger this rule: `Insider` nodes carry no
`MEMBER_OF` edges, so the committee-oversight path cannot exist for them.
