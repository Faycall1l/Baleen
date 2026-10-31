# Contributing

Thanks for taking a look. Baleen is a small, opinionated codebase; the notes
below are what keeps it reviewable.

## Before you start

- Open an issue for anything larger than a typo or a one-line fix, so the
  design can be agreed before code exists.
- Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and
  [docs/SCHEMA.md](docs/SCHEMA.md). The pipeline boundaries are deliberate.
- Data sources and their terms are listed in [docs/SOURCES.md](docs/SOURCES.md).
  Do not add a source that forbids automated access or redistribution.

## Local setup

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec backend python -m scripts.seed_reference
docker compose exec backend python -m scripts.ingest_once --real
```

The keyless feeds need no credentials. An LLM endpoint is optional: without
one, structured filings still become events; free text stays pending.

## Checks

```bash
# backend
cd backend
pip install -r requirements-dev.txt
ruff check . && ruff format --check .
TEST_DATABASE_URL="postgresql+psycopg://baleen:baleen@localhost:5433/baleen_test" \
  python -m pytest tests -q

# frontend
cd frontend
npm ci
npm run lint && npm test && npm run build
```

CI runs the same commands. A pull request should be green before review.

## What a good change looks like

- **One concern per PR.** A bug fix does not carry a refactor; a feature does
  not reformat unrelated files.
- **Tests where behaviour changes.** Backend tests live next to the module they
  cover; frontend tests are colocated `*.test.ts(x)`.
- **Traceability stays intact.** Every graph node and edge must keep a source
  URL. Do not invent identifiers when a filing supplies one.
- **No secrets, no personal data.** `.env` is ignored; examples use
  placeholders. Do not commit credentials, private contact details or scraped
  personal data.
- **The model is never trusted with structure.** New extraction paths should
  ground identifiers deterministically where the source provides them.
- **Keep the query agent guarded.** New labels or relationships must be added
  to the allowlist in `backend/app/query/` and covered by a validation test.

## Commit messages

Short imperative subject, then a body explaining *why* when the change is not
obvious. Reference the issue number when there is one.

## Licence

By contributing you agree that your contribution is licensed under the MIT
licence in [LICENSE](LICENSE).
