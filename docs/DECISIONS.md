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
| D12 | 2026-11-14 | Login rate limiting uses a small in-house limiter (`app/core/rate_limit.py`) instead of slowapi | The plan wants limits per IP **and per account**, counting only failures; slowapi can only key on the request (IP). Counts live in memory: fine for one API process, would need Redis for several |
| D13 | 2026-11-14 | Strict refresh-token rotation: any reuse revokes the whole family | Plan section 5.1. Side effect: two browser tabs refreshing at the exact same moment can log the user out. The web client (Stage 13) must share one refresh call between tabs |
| D14 | 2026-11-14 | Logout doesn't require a valid access token | A user whose access token has already expired must still be able to log out; revoking a token you hold is harmless |
| D15 | 2026-11-14 | Refresh cookie is `SameSite=Strict` + Origin check | Protects against CSRF. Requires the website and API to be on the same site in production (e.g. `freshlink.pk` and `api.freshlink.pk`); revisit when hosting is chosen (plan decision 6) |

| D16 | 2026-11-14 | Stage 2 split into 2a (categories + profiles) and 2b (documents + verification + admin review) | Keeps each stage small and testable |
| D17 | 2026-11-14 | Small food businesses are **allowed** as receivers, with a mandatory not-for-resale declaration and the lowest placeholder priority (0.5) | Plan section 12, decision 3, first option. **Still needs supervisor confirmation**; an admin can deactivate the category if excluded |
| D18 | 2026-11-14 | Categories have a fixed `code` (used by program rules) and an editable `name` (shown to users) | Admins can rename "Small food business" without breaking the not-for-resale rule |
| D19 | 2026-11-14 | Receiver priority weights seeded as placeholders: NGO 1.0, community kitchen 1.0, community group 0.8, individual 0.7, small business 0.5 | Needed by matching/allocation later; admins adjust them in the admin stage |
| D20 | 2026-11-14 | Profile PUT endpoints create-or-update ("upsert"), always on `/me` | One endpoint serves both the registration wizard and "Edit profile"; `/me` prevents reading other users' profiles |
| D21 | 2026-11-14 | Latitude/longitude stored as double precision; quantities as NUMERIC(10,2) | Coordinates need no exact decimals; quantities do |

| D22 | 2026-11-14 | Unexpected crashes are caught by a middleware placed **inside** the CORS middleware | FastAPI's catch-all handler runs outside CORS, so 500 responses had no CORS header and the website would only see a "CORS error" |
| D23 | 2026-11-14 | The refresh cookie is also accepted from the API's own origin | The Swagger page (same origin = no CSRF risk) could not refresh or log out |
| D24 | 2026-11-14 | OpenAPI is post-processed: every 422 uses `ErrorResponse`; secured endpoints document 401/403; operation ids look like `auth-login` | The website/mobile TypeScript client is generated from this document (plan section 2.2), so it must match reality |

| D25 | 2026-11-14 | Login limits split: **5 failures per account**, **20 per IP**, per minute (`LOGIN_MAX_FAILURES_PER_ACCOUNT`, `LOGIN_MAX_FAILURES_PER_IP`) | Pakistani mobile networks use carrier-grade NAT (many users share one IP). With 5 per IP, a few typos by strangers could lock out a whole network. Per-account still stops guessing one password |
| D26 | 2026-11-14 | Command-line output is plain ASCII (`[OK]`, `[x]`) | `create-admin` crashed in Windows terminals using code page 1252 when printing a check mark |

## API review after Stage 2a (2026-11-14)

All 25 endpoints were reviewed: coverage went from 98% to 99% (the remaining 5 lines are trivial), 4 problems were fixed (message prefix, wrong 422 docs, missing CORS header on 500, Swagger cookie flow), and every fix has a test that was shown to fail without it.

## Known follow-ups

- Expired/revoked refresh-token rows are never deleted yet. A clean-up job belongs in the worker process (plan section 2.1, worker stage).
- Stage 2b must decide what happens when an **approved** user edits key profile details (e.g. business name or category): keep the approval, or send the profile back for review.
- The "allow small business receivers" platform setting (plan section 3.3) arrives with `platform_settings` in the admin stage; until then the category's `is_active` flag controls it.
- Behind a hosting proxy, the client IP for rate limiting must come from `X-Forwarded-For` (`uvicorn --proxy-headers`). Configure this at deployment.

## Open (need the team's or supervisor's answer)

See Technical Plan §12 and the current stage notes in the README.
