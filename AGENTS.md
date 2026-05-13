# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## What this repo is

**bid-app**: a Chinese-language 投标技术方案 (technical-proposal) generator. Users upload up to 3 documents (tech spec / scoring rules / template), a LangGraph workflow drives outline → per-chapter generation → human review (3-button pass / revise / skip) → DOCX export. Self-hosted single-host deployment via Docker Compose; 10-user internal shared pool is the design target.

Repo layout:

- `app/` — the actual product. **All code, build, tests, and deploys live under here**. `cd app/` before any docker / build / test command (compose otherwise mis-reads project root and `.env` resolves to empty strings — postgres won't start).
- `app/backend/` — FastAPI + arq + LangGraph (`uv` + Python 3.12, package `bid_app`).
- `app/frontend/` — Vite + React 18 + TS + TanStack Query + shadcn/ui (pnpm 9.15).
- `app/docker/`, `app/docker-compose*.yml`, `app/scripts/` — single-image deploy (supervisord runs uvicorn + arq + cron) + ops scripts.
- `app/IMPLEMENTATION_SPEC.md` (~7100 lines, §1–§24, decision points D-A…D-EE) is the authoritative design doc. `app/REQUIREMENTS.md` / `app/RUNTIME_TEST_REPORT.md` / `app/REVIEW_NOTES.md` / `app/ACCEPTANCE_AUDIT.md` are the supporting record. When in doubt about *why* something is shaped a certain way, grep the spec for the matching `D-*` tag.
- Top-level `README.md`, `USER_GUIDE.md`, `restart.sh` (delegates to `app/scripts/restart-after-update.sh`).

## Common commands

All commands assume `cd app/` first.

### Local dev (host-machine backend/frontend, db + redis in compose)

```bash
./scripts/gen-secrets.sh                       # generate .env (master_key, JWT secret, postgres pw)
docker compose -f docker-compose.dev.yml up -d # db + redis only (data → ./.dev-data/)

cd backend && uv sync --all-extras && uv run alembic upgrade head
uv run uvicorn bid_app.main:app --reload --port 12123 --host 127.0.0.1    # terminal A
uv run arq bid_app.worker.settings.WorkerSettings                          # terminal B

cd ../frontend && pnpm install && pnpm dev      # terminal C (5173, proxies /api → 12123)
```

### Docker (full stack, prod-like)

```bash
docker compose up -d
docker compose ps                               # wait for (healthy)
docker compose logs -f app | jq -c .
./scripts/restart-after-update.sh               # rebuild + restart + healthcheck after git pull
```

### Backend lint / test (run in `app/backend`)

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src                                 # strict, with pydantic plugin
uv run pytest                                   # all
uv run pytest tests/test_markdown_visuals.py::test_merge_chapter_unwraps_fenced_mermaid_visual -v  # single test
```

Tests that touch the DB require a `${POSTGRES_DB}_test` database — on a non-fresh data volume run `./scripts/create-test-db.sh` (the docker entrypoint init script only fires on an empty volume; D-DV). The `db_engine` fixture refuses to operate on a db whose name doesn't end in `_test` (D-EA).

### Frontend lint / build (run in `app/frontend`)

```bash
pnpm lint        # eslint, --max-warnings 0
pnpm build       # tsc -b && vite build
pnpm format
```

There is no frontend test runner.

### Ops / emergency

```bash
docker compose exec app python -m bid_app.cli.reset_admin --password new_pass
docker compose exec app python -m bid_app.cli.test_llm --api-key sk-xxx     # M0 LLM connectivity check
docker compose exec app /usr/local/bin/pg-backup.sh                          # manual backup (cron does it 03:00 Asia/Shanghai daily)
./scripts/restore-backup.sh /var/lib/bid-app/backups/bid_YYYYMMDD_HHMM.dump  # destructive; prompts for confirm
./scripts/sync-postgres-password.sh                                          # .env vs old postgres volume password drift
```

## Architecture (load-bearing pieces)

### LangGraph workflow (`app/backend/src/bid_app/workflow/`)

11 nodes, registered in `graph.py`, ordered:

```
extract_documents → generate_outline → outline_review (interrupt)
  → parse_outline → pick_chapter → write_chapter → gen_visuals
  → merge_chapter → human_review (interrupt) → update_state → assemble
```

- `outline_review` and `human_review` are **interrupt nodes** — execution suspends and the SSE stream waits for a `POST /chapters/{id}/decision` (pass/revise/skip) from the UI, then `update_state` increments `current_index` (pass/skip) or keeps it and bumps `retry_count` (revise).
- State is the Pydantic `WorkflowState` in `workflow/state.py`. **Renaming any field on it requires touching `nodes/*.py`, the `AsyncPostgresSaver` checkpoint compatibility window, and probably an alembic migration** — see `IMPLEMENTATION_SPEC §9`.
- Three LLMs: LLM-1 outline, LLM-2 body (streamed), LLM-3 Mermaid visuals. All routed through `services/llm.py` → LiteLLM → DashScope (OpenAI-compatible). Per-request retry + token accounting (`services/token_usage.py`).
- LangGraph checkpointer is `AsyncPostgresSaver`. **Restarts resume mid-workflow** from the latest checkpoint; if you change node names or state schema, old checkpoints can refuse to resume.

### Workers & retries

`arq` worker config in `worker/settings.py`. **All four task types are `max_tries=1`** (D-Z / D-AY): start_workflow, resume_review, retry_failed_chapter, generate_docx. Failure is surfaced to the user for a manual retry — never silently retried by the queue. The wrapper form is `func(coroutine, max_tries=1)` (positional coroutine), not `@func(max_tries=1)` — arq 0.26's decorator API requires it. `on_startup` reconciles orphaned chapters left mid-flight by a crash (`worker/lifecycle.py`).

### Auth & API Key crypto (security-critical)

- JWT in httpOnly cookie; bcrypt password hashes (**`bcrypt < 5` pin is required** — passlib 1.7.4 detect-wrap-bug crashes on bcrypt 5.0; see `pyproject.toml` comment).
- DashScope API keys are stored AES-GCM encrypted with `BID_APP_MASTER_KEY` (64-hex, `secrets.token_hex(32)`). **Losing this key permanently bricks every encrypted ApiKey row, including `Project.encrypted_api_key_snapshot`** (R10). Startup banner prints the sha256 prefix — operators must compare against their password-manager backup. Rotation is `scripts/rotate_master_key.py --confirm` (§24.3); never edit `BID_APP_MASTER_KEY` in `.env` without rotating first.
- Project "real snapshot" (FR-7.6 / D-C): when a project starts, the user's *current* ApiKey is re-encrypted into `Project.encrypted_api_key_snapshot`. Later changes/deletions to the ApiKey row do not break the running project; workflow nodes always read the snapshot.
- Force-change-password (HTTP 428) gate, login-throttle (5 failures → 5-min IP lock), global 100 req/min/IP, CSP / X-Frame-Options / X-Content-Type-Options headers.

### DOCX export

`services/docx_export.py` + `templates/reference.docx` + Pandoc + Mermaid CLI + Chromium + Noto CJK + LibreOffice headless. The pipeline is **serial-locked across the whole container** (single global lock) with atomic rename and a finalizing-state safety net (DocxJob state machine D-CV / D-CU / D-BX). Don't parallelize this — Mermaid CLI's Chromium and LibreOffice both break under concurrency.

### Frontend

- `src/router.tsx` defines the 10 routes; pages in `src/pages/` (`OutlineConfirmPage`, `ChapterReviewPage`, `ProposalPage` are the human-in-the-loop UI).
- SSE stream consumer in `src/hooks/` (token-by-token render); Mermaid renders client-side with a `mermaid.live` fallback when the LLM emits unparseable syntax.
- A mock mode exists in `src/api/` for working without a backend.

## Conventions worth knowing

- **Decision tags `D-A`…`D-EE`**: every non-obvious design choice in the codebase is anchored to a tag in `IMPLEMENTATION_SPEC`. When making a change that contradicts a tag, update the spec — don't leave the code and spec out of sync.
- **Exception class names** (`SlotLost`, `LLMRetryFailed`, `ChapterGenerationFailed`, …) are spec markers, **not** lint-error suffixes. `N818` is globally suppressed; don't "fix" them.
- **Prompts in `workflow/prompts/`** contain intentional Chinese typography (× ÷ —). `RUF001/002/003` ambiguous-unicode warnings are suppressed for that path; don't ASCII-fy.
- **`mypy` strict** is on for the whole package, with narrow per-module suppressions for redis/langgraph stub mismatches (see `pyproject.toml`). Don't widen the suppressions casually.
- **`pyproject.toml` pins** carry inline comments explaining *why*. Read them before bumping versions.
- **Migrations are mandatory and synchronous on boot**: `docker/entrypoint.sh` runs `alembic upgrade head` before supervisord starts uvicorn (D-O). A failed migration → container restart loop, not silent skew.
- **Frontend ESLint `--max-warnings 0`** is enforced; CI-style strictness on the host too.
- **`master_key` and `.env`**: `scripts/install.sh` backs `.env` up to `/var/lib/bid-app/.env` on first install; re-installs auto-restore, and refuse to start if the restored `BID_APP_MASTER_KEY` doesn't match the new `.env` (avoids silently bricking historical ApiKey rows).

## Known limits (don't try to "fix" without scope discussion)

Single-instance, no HA; PG volume loss = checkpoint loss (daily `pg_dump -F c` cron mitigates); `.pdf` / `.ppt` not supported by markitdown+LibreOffice path — users convert to `.docx` first; Mermaid client-side rendering occasionally fails on rare LLM syntax (fallback to source view is intentional).
