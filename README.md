# FreshLink
FreshLink - AI-powered food donation and redistribution platform

An AI-driven, date-aware food surplus allocation and redistribution system.
It has one shared **FastAPI** backend and **PostgreSQL** database, used by a **React website**
and a **React Native (Expo) Android app**.

## Project status
Development started. Each stage is added and tested one at a time.

| Stage | Status |
|---|---|
| 0a. Repository scaffold + backend skeleton | Done |
| 0b. PostgreSQL (Docker), SQLAlchemy, Alembic migrations, error format, DB health check | Done |
| 1a. Users table, register, login, `/auth/me`, role checks, create-admin command | Done |
| 1b. Refresh tokens (rotation + reuse detection), logout, logout-all, change password, login rate limiting | Done |
| 2a. Categories (seeded), public lookups, provider/receiver/volunteer profiles, receiver food preferences (capacity), volunteer availability, account update | Done |
| 2b. Private document upload, verification submit/status, admin queue with approve / reject / request info, receiver eligibility, append-only audit log | Done |
| 3a. Food listings: create / view / edit (optimistic locking) / pause / resume / close / remove, category date rules, food photos, status history | Done |
| 3b. Urgency engine (rule-based, per-category thresholds, explanations), admin urgency rules, expiry scan (`python -m app.cli urgency-scan`) | Done |
| 4a. Receiver browse / search: typo-tolerant name search, category, distance (Haversine) with saved location and pickup radius, quantity + unit, pickup time, urgency; 5 sort orders; only food that can still be collected | Done |
| 4b. Food requests: create (eligibility, availability, quantity and capacity checks), role-scoped lists, edit / cancel, provider reject with reason, incoming requests per listing, requests expire when a listing ends | Done |
| 5a. Allocation: explainable suggestions (priority-weighted / proportional-fair / first-come) with reproducible snapshots, confirm with overrides + reasons in one locked transaction, approve shortcut, cancel; spec example gives 15 / 10 / 5 | Done |
| 5b. Matching v1 (explainable heuristic): ranked receivers with score parts and reasons, explained exclusions, "Recommended for you", match scores on incoming requests, stored snapshots with outcomes | Done |
| 6. Collection and volunteers: handover, receipt, volunteer assignments and status tracking | Next |

There's no website or mobile UI yet.

## Create the first administrator
Admins can't sign up through the website or app. Create the first one on your own computer
(from `backend/`, with the venv active). You'll be asked for the details and a hidden password:

```powershell
python -m app.cli create-admin
```

## Database: first-time setup (Docker Desktop must be running)

```powershell
cd D:\University\FreshLink
Copy-Item .env.example .env        # then set your own POSTGRES_PASSWORD in .env
docker compose up -d               # start PostgreSQL 17
docker compose ps                  # status should say "healthy"
```

Then apply the migrations (from `backend/`, with the venv active): `alembic upgrade head`

- `docker compose stop` stops the database. Your data is kept.
- `docker compose down -v` **deletes all database data**. Only use it if you really mean to.
- Tests use a separate `freshlink_test` database that is created automatically.

## Repository layout

| Folder | What it is | Status |
|---|---|---|
| `backend/` | FastAPI REST API (`/api/v1`) shared by web and mobile | Skeleton running |
| `web/` | React website | Not started |
| `mobile/` | Expo Android app | Not started |
| `packages/` | Code shared by web + mobile (API client, enums) | Not started |
| `ml/` | ML training, synthetic data, evaluation | Not started |
| `docs/` | `PLAN.md` (Technical Plan), `DECISIONS.md` | — |

## Backend: first-time setup (Windows, PowerShell)

Needs **Python 3.14** (check with `python --version`).

```powershell
cd D:\University\FreshLink\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
alembic upgrade head
```

If PowerShell says running scripts is disabled, run this once and try again:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## Backend: daily use

```powershell
cd D:\University\FreshLink\backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload     # start the API at http://127.0.0.1:8000
pytest                            # run all tests (needs the database running)
pytest --cov=app --cov-report=term-missing   # also show which code lines no test runs
```

- Health check: http://127.0.0.1:8000/api/v1/health (returns 503 if the database is down)
- Interactive API docs (Swagger): http://127.0.0.1:8000/docs

## Rules for the team
- Never commit `.env` files, passwords or API keys. Only `.env.example` goes into Git.
- Work on a branch per feature and open a Pull Request into `main`.
- Run `pytest` before pushing.
