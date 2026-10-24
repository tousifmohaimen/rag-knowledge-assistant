# RAG Knowledge Assistant

A multi-tenant document Q&A service. Companies upload their documents; their users ask questions and get answers that cite the exact page, or a clear "I couldn't find that" when the documents don't cover it.

> **Status: in active development.** The foundation (database, multi-tenant security, authentication, CI) is done. Document ingestion and question answering are next. See the [roadmap](#roadmap).

## What makes it different

- **Answers you can check.** Every answer cites the document and page it came from.
- **Hybrid retrieval.** Vector search plus keyword search, merged and re-ranked, so exact terms such as product codes aren't missed.
- **Measured, not guessed.** An evaluation set tracks retrieval hit rate, faithfulness and refusal accuracy over time.
- **Tenant isolation enforced twice.** Every query is filtered by tenant in code, and Postgres row-level security blocks other tenants' rows even if the code forgets.

## Tech stack

| Layer | Choice |
| --- | --- |
| API | Python 3.12, FastAPI, Pydantic |
| Database | PostgreSQL 16 + pgvector, SQLAlchemy 2 (async), Alembic |
| Auth | JWT access and refresh tokens, Argon2 password hashing |
| Tooling | uv, ruff, mypy (strict), pytest |
| Delivery | Docker, GitHub Actions |

## How tenant isolation works

Each request runs in a database session that first sets `app.tenant_id` for its transaction. Row-level security policies on every tenant table compare each row's `tenant_id` with that setting:

```sql
CREATE POLICY tenant_isolation ON documents
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
```

The API connects as a role that does not own the tables, so the policies always apply to it. If no tenant is set, no rows are visible. `tests/integration/test_tenant_isolation.py` proves this with queries that deliberately leave out the tenant filter.

## Running it locally

**Requirements:** Docker, and [uv](https://docs.astral.sh/uv/) for development.

```bash
git clone <your-repo-url> rag-knowledge-assistant
cd rag-knowledge-assistant
cp backend/.env.example backend/.env    # then set JWT_SECRET to a long random string

docker compose -f infra/docker-compose.yml up --build
```

This starts Postgres, runs the migrations, and serves the API at <http://localhost:8000>. Interactive API docs are at <http://localhost:8000/docs>.

Create a company and its first admin user (you'll be asked for a password):

```bash
docker compose -f infra/docker-compose.yml run --rm api python -m scripts.seed \
  --tenant "Acme Pty Ltd" --email admin@acme.com
```

Log in:

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@acme.com", "password": "<your password>"}'
```

## Development

Run Postgres in Docker and the API on your machine:

```bash
docker compose -f infra/docker-compose.yml up -d db
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Checks (the same ones CI runs):

```bash
uv run ruff check .          # lint
uv run ruff format --check . # formatting
uv run mypy                  # strict type checking
uv run alembic check         # migrations match the models
uv run pytest                # unit and integration tests (needs the db container)
```

The tests use a separate `rka_test` database and refuse to run against any database whose name doesn't end in `_test`.

## Project structure

```text
backend/
  app/
    api/v1/        HTTP routes
    core/          settings, security, logging
    db/            models and sessions
    schemas/       request and response models
    services/      business logic
  migrations/      Alembic migrations
  scripts/         admin scripts (seeding)
  tests/           unit and integration tests
infra/             Docker Compose and Postgres setup
.github/workflows/ CI
```

## API

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/v1/health` | Health check, including the database |
| POST | `/api/v1/auth/login` | Exchange email and password for tokens |
| POST | `/api/v1/auth/refresh` | Exchange a refresh token for new tokens |
| GET | `/api/v1/auth/me` | The current user |

## Roadmap

- [x] Project setup, Docker Compose, CI
- [x] Data model with row-level security
- [x] Authentication with access and refresh tokens
- [ ] Document upload and background ingestion (parse, chunk, embed)
- [ ] Hybrid search with re-ranking
- [ ] Streamed answers with page-level citations and a confidence gate
- [ ] Evaluation set, metrics and dashboard
- [ ] React frontend
- [ ] Rate limiting and refresh-token revocation
- [ ] Cloud deployment

## License

MIT
