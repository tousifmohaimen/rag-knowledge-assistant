# RAG Knowledge Assistant

A multi-tenant document Q&A service. Companies upload their documents; their users ask questions and get answers that cite the exact page, or a clear "I couldn't find that" when the documents don't cover it.

> **Status: in active development.** Multi-tenant security, authentication and document ingestion are done. Search and question answering are next. See the [roadmap](#roadmap).

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
| Background jobs | Redis + arq worker |
| Ingestion | pypdf, heading-aware chunking, OpenAI embeddings (or an offline embedder) |
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

## How document ingestion works

Uploads return straight away (`202 Accepted`); a background worker does the slow part.

1. **Upload.** The file is streamed to disk while it is hashed and size-checked. Its type is decided from its bytes (PDF magic bytes, or valid UTF-8 for `.txt`/`.md`), never from the client's `Content-Type`. Uploading the same file twice returns `409` with the existing document's ID.
2. **Queue.** The document is saved as `queued` and a job is put on Redis, only after the database commit, so the worker can always find it.
3. **Parse.** Text is extracted page by page so every chunk keeps the page number it will be cited with.
4. **Chunk.** Paragraphs are packed into chunks of about 600 tokens with about 100 tokens of overlap. Chunks never cross a page boundary, and each remembers the latest heading as its `section`. Repeated chunks (headers, footers, boilerplate) are stored once.
5. **Embed.** Chunks are embedded in batches, retrying with backoff on rate limits and server errors.
6. **Store.** Old chunks are replaced and the document is marked `ready` in one transaction, so search never sees a half-indexed document. Failures are recorded on the document with a message that is safe to show users.

### Embeddings without an API key

`EMBEDDING_PROVIDER=fake` (the default) uses a hashing embedder that needs no key or network: texts that share words get similar vectors. It is good enough to develop and test with, but it doesn't understand meaning. Set `EMBEDDING_PROVIDER=openai` and `OPENAI_API_KEY` for real semantic search with `text-embedding-3-small`.

## Running it locally

**Requirements:** Docker, and [uv](https://docs.astral.sh/uv/) for development.

```bash
git clone <your-repo-url> rag-knowledge-assistant
cd rag-knowledge-assistant
cp backend/.env.example backend/.env    # then set JWT_SECRET to a long random string

docker compose -f infra/docker-compose.yml up --build
```

This starts Postgres, Redis, the migrations, the API at <http://localhost:8000> and the background worker. Interactive API docs are at <http://localhost:8000/docs>.

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

Run Postgres and Redis in Docker, and the API and worker on your machine:

```bash
docker compose -f infra/docker-compose.yml up -d db redis
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload        # terminal 1
uv run arq app.workers.worker.WorkerSettings # terminal 2
```

Checks (the same ones CI runs):

```bash
uv run ruff check .          # lint
uv run ruff format --check . # formatting
uv run mypy                  # strict type checking
uv run alembic check         # migrations match the models
uv run pytest                # unit and integration tests (needs the db container)
```

The tests use a separate `rka_test` database and refuse to run against any database whose name doesn't end in `_test`. They don't need Redis or an API key: the queue is replaced by one that records jobs, and the pipeline tests use the offline embedder.

## Project structure

```text
backend/
  app/
    api/v1/        HTTP routes
    core/          settings, security, logging, file storage
    db/            models and sessions
    ingestion/     parsing, chunking, embeddings, pipeline
    schemas/       request and response models
    services/      business logic
    workers/       job queue and background worker
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
| POST | `/api/v1/documents` | Upload a PDF, `.txt` or `.md` file (admins) |
| GET | `/api/v1/documents` | List your company's documents, paginated |
| GET | `/api/v1/documents/{id}` | One document and its processing status |
| POST | `/api/v1/documents/{id}/reprocess` | Run ingestion again (admins) |
| DELETE | `/api/v1/documents/{id}` | Delete a document, its chunks and its file (admins) |

## Roadmap

- [x] Project setup, Docker Compose, CI
- [x] Data model with row-level security
- [x] Authentication with access and refresh tokens
- [x] Document upload and background ingestion (parse, chunk, embed)
- [ ] Hybrid search with re-ranking
- [ ] Streamed answers with page-level citations and a confidence gate
- [ ] Evaluation set, metrics and dashboard
- [ ] React frontend
- [ ] Rate limiting and refresh-token revocation
- [ ] OCR for scanned PDFs
- [ ] Cloud deployment

## License

MIT
