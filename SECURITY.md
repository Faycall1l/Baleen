# Security policy

## Supported versions

Baleen is developed on `main`; only the latest commit is supported. There are
no maintained release branches yet.

## Reporting a vulnerability

Please do **not** open a public issue for a security problem. Use GitHub's
private vulnerability reporting:

<https://github.com/Faycall1l/Baleen/security/advisories/new>

Include the affected commit, a minimal reproduction, and the impact you
believe it has. You can expect an acknowledgement within a few days.

## Scope

The parts of the system with a meaningful attack surface are:

- **The query agent** (`backend/app/query/`). Model-generated Cypher is checked
  against a label/relationship allowlist and rejected if it contains a write
  clause or procedure call, then executed with the writer driver or, when
  configured, a dedicated read-only Neo4j role. Bypasses of that validation are
  in scope.
- **Ingestion** (`backend/app/ingestion/`). Sources fetch remote documents and
  parse PDFs/JSON. SSRF, unbounded downloads, or parser crashes caused by
  hostile input are in scope.
- **Secrets handling**. `.env` is git-ignored and the app never logs keys.
  Accidental credential exposure is in scope.

Out of scope: findings that require a modified local checkout, denial of
service against your own machine, and the accuracy of third-party data.

## Data and privacy

Baleen works with public records (US congressional disclosures, SEC filings and
delayed market data). It does not collect private personal data. Source terms
and attribution are documented in [docs/SOURCES.md](docs/SOURCES.md).
