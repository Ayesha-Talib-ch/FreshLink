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

| D27 | 2026-11-14 | An **approved** user who edits their profile **stays approved** | Simplest rule for v1; a "send back for review on key changes" rule can be added if the supervisor wants it. **Needs supervisor confirmation** |
| D28 | 2026-11-14 | Document requirements are seeded per role / receiver category (provider: business proof + optional shop photo; NGO: registration certificate; community kitchen/group: organization proof; individual + volunteer: CNIC; small business: business proof) | Minimum data needed (plan issue M7); admins can change them in the admin stage |
| D29 | 2026-11-14 | File type is detected from the file's first bytes (PDF/PNG/JPEG only), max 5 MB, max 30 files per user; files stored under random names in a git-ignored private folder | Never trust the file name or the browser's claimed type; prevents path traversal |
| D30 | 2026-11-14 | Strangers get **404** (not 403) for files they may not read | Doesn't reveal that the file exists (plan section 5.3) |
| D31 | 2026-11-14 | Audit log is append-only via a database trigger; `actor_id` has no foreign key | Even an application bug can't rewrite history; entries survive user deletion. In production the app's DB role should also lack TRUNCATE |
| D32 | 2026-11-14 | Notifications for verification results are **not sent yet** | The notifications feature (plan Phase 2) will add "verification approved/rejected" messages; until then the user sees the result on the Verification screen |

| D33 | 2026-11-14 | Stage 3 split into 3a (listings) and 3b (urgency engine + automatic expiry) | The urgency engine is its own module with many boundary tests |
| D34 | 2026-11-14 | All listing times must include a time zone (`+05:00`); times without one are refused | A guessed zone would shift every deadline by up to 5 hours (plan issue M11) |
| D35 | 2026-11-14 | Listing date rules: category flags decide required dates (cooked meals/fast food: preparation time; dairy/packaged: expiry date); preparation/packaging can't be in the future; expiry/best-before and the deadline can't have passed; deadline at most 30 days ahead and not after the expiry date | Spec 9.6 and plan 1.3. The 30-day limit catches typos such as the year 2099 |
| D36 | 2026-11-14 | Editing uses optimistic locking: `If-Match: <version>` is **required**; a stale version gives 409 `STALE_VERSION` with the current version | Plan issue H5 / section 2.4; two devices can't silently overwrite each other |
| D37 | 2026-11-14 | Removing a listing is a soft delete (status `removed`); it stays in the database for history and reports | Plan section 3.1 ("history is never hard-deleted") |
| D38 | 2026-11-14 | Listing detail is visible to the owner, admins, and **verified** receivers while it's **active**; the public provider card shows only the business name | Spec 15: contact details only within an authorized transaction. Receiver browse/search comes in the receiver stage |
| D39 | 2026-11-14 | Food photos (`listing_image`) are JPG/PNG only and readable by any logged-in user; verification documents stay private | A photo on a listing is public by nature |

| D40 | 2026-11-14 | Urgency = **rule-based decision support**, a pure engine (`app/engines/urgency.py`): effective deadline = earliest of collection deadline, expiry, best-before, preparation + category limit; thresholds inclusive (exactly 6 h left = high) | Plan section 7.3; label it honestly in the report as "rule-based", not ML (plan 1.4) |
| D41 | 2026-11-14 | Placeholder thresholds (medium/high/critical h, max h since preparation): bakery 48/24/6/72, cooked 12/6/2/12, fast food 12/6/2/8, produce 72/24/8/–, dairy 72/24/6/–, packaged 168/48/12/–, other 48/24/6/– | Demo values for prioritisation, **not food-safety limits**; admins change them via `PUT /admin/urgency-rules/{category}` (audited) |
| D42 | 2026-11-14 | Missing required dates give `incomplete`, never `normal`; every result carries an explanation and a disclaimer ("not a food-safety assessment"); wording never says safe/fresh/edible | Spec 6.2 food-safety limitation; plan section 1.3 |
| D43 | 2026-11-14 | Urgency is calculated live on every read; the stored `urgency_level` is for SQL filtering and is refreshed by writes and the scan | Responses are never stale; filtering stays fast |
| D44 | 2026-11-14 | The expiry scan is idempotent and uses `SKIP LOCKED`; run by `python -m app.cli urgency-scan` until the worker process exists | Plan issue M8: the scheduler belongs in one separate worker process |
| D45 | 2026-11-14 | Receivers stop seeing a listing the moment its effective deadline passes, even before the scan runs | No one should request food that can no longer be collected |
| D46 | 2026-11-14 | `urgency_rules.updated_by` has no foreign key | A FK to `users` made the test clean-up (TRUNCATE … CASCADE) wipe the seeded rules; the audit log already records who changed what. A guard test now checks that no seeded table points at an emptied table |

| D47 | 2026-11-14 | Stage 4 split into 4a (browse/search) and 4b (requests) | Keeps each stage small and testable |
| D48 | 2026-11-14 | Distance = Haversine straight-line km, calculated in SQL (`app/engines/geo.py`, with an identical Python version and a test that both agree) | Plan issue M3: no paid map API; road distance would need an external service |
| D49 | 2026-11-14 | Without `near_lat/near_lng`, browse uses the receiver's saved location and pickup radius; `radius_km` overrides it (max 100 km) | Spec 6.3 "receiver location"; a receiver can widen the search, e.g. if they have transport |
| D50 | 2026-11-14 | "Available" = active, quantity left, and no collection/expiry/best-before/preparation-limit deadline passed, checked in SQL | No one sees food that can no longer be collected, even before the expiry scan runs |
| D51 | 2026-11-14 | Name search uses ILIKE (with `%`/`_` escaped) plus trigram `word_similarity > 0.35` | Typo tolerance ("biryni" finds "biryani") using the pg_trgm index from migration 0006 |
| D52 | 2026-11-14 | A minimum-quantity filter requires a unit | Plan issue H1: 10 kg and 10 packages are not comparable |
| D53 | 2026-11-14 | The urgency filter/sort uses the stored level (refreshed by writes and the scan); the response shows the live level | Fast SQL filtering; at most one scan interval behind |
| D54 | 2026-11-14 | Admins may also use `GET /listings` (monitoring); providers and volunteers may not | Spec: browsing is a receiver feature; full admin listing management comes in the admin stage |

## API review after Stage 2a (2026-11-14)

All 25 endpoints were reviewed: coverage went from 98% to 99% (the remaining 5 lines are trivial), 4 problems were fixed (message prefix, wrong 422 docs, missing CORS header on 500, Swagger cookie flow), and every fix has a test that was shown to fail without it.

## Known follow-ups

- Expired/revoked refresh-token rows are never deleted yet. A clean-up job belongs in the worker process (plan section 2.1, worker stage).
- Stage 2b must decide what happens when an **approved** user edits key profile details (e.g. business name or category): keep the approval, or send the profile back for review.
- The "allow small business receivers" platform setting (plan section 3.3) arrives with `platform_settings` in the admin stage; until then the category's `is_active` flag controls it.
- Uploaded files of deleted users stay on disk (their database rows are deleted). A clean-up job and a document-retention setting (plan issue M7) belong to the worker/admin stages.
- The worker process (plan section 2.1) must run the urgency scan every 5 minutes; until then run `python -m app.cli urgency-scan` by hand (or with Windows Task Scheduler for demos).
- Behind a hosting proxy, the client IP for rate limiting must come from `X-Forwarded-For` (`uvicorn --proxy-headers`). Configure this at deployment.

## Open (need the team's or supervisor's answer)

See Technical Plan §12 and the current stage notes in the README.
