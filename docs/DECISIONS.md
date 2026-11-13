# FreshLink — Decision Log

The master specification and the Technical Plan (`docs/PLAN.md`) are the sources of truth.
This file records decisions taken during development and any questions that are still open.

## Decided

| # | Date | Decision | Reason |
|---|---|---|---|
| D1 | 2026-11-14 | Use Python 3.14 (plan said 3.12) | Installed on the dev machine; every planned library (FastAPI, SQLAlchemy, psycopg, scikit-learn, pandas, OR-Tools) has 3.14 wheels. All team members must use the same version. |
| D2 | 2026-11-14 | Technical Plan's schema/API/team split supersedes the spec's proposals where they differ | The spec says its endpoint names are "not a fixed contract"; the plan's changes fix real data-integrity issues (plan §3.4) |
| D3 | 2026-11-14 | One monorepo: `backend/`, `web/`, `mobile/`, `ml/`, `packages/`, `docs/` | Plan §8 |
| D4 | 2026-11-14 | PostgreSQL 17 runs in Docker Desktop (`docker-compose.yml`), not installed natively | Team choice; every member gets the identical DB version |
| D5 | 2026-11-14 | Connect to `127.0.0.1`, not `localhost` | On Windows, `localhost` tries IPv6 first and every connection waited ~5 s |
| D6 | 2026-11-14 | Tables are created feature by feature (one migration per stage), not all at once in Phase 0 | Matches the one-feature-at-a-time process; each table is tested when the feature using it is built |
| D7 | 2026-11-14 | DB password lives only in the root `.env` (shared by Docker and the backend) | One place to change it; nothing secret in Git |
| D8 | 2026-11-14 | Authentication split into 1a (register/login/me/roles/create-admin) and 1b (refresh tokens, logout, change password, rate limiting) | Keeps each stage small and testable |
| D9 | 2026-11-14 | Phone numbers stored in E.164 (`+923001234567`); Pakistani local formats converted automatically | Otherwise `0300-1234567` and `+92 300 1234567` would count as different numbers |
| D10 | 2026-11-14 | Registration creates the account only; role profile details (business, category, location, documents) come in Stage 2 | Profiles + verification are their own feature (plan section 4.3); the register wizard will call both |
| D11 | 2026-11-14 | Phone is required at registration | The spec asks every role for contact details; it is also a login identifier |

## Open (need the team's or supervisor's answer)

See Technical Plan §12 and the current stage notes in the README.
