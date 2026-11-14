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

| D55 | 2026-11-14 | A request does not reserve food; approval and quantities are decided by the allocation stage | Spec 5.5: when requests exceed supply, an allocation suggestion divides the food; approved quantity lives on the allocation (plan 3.4) |
| D56 | 2026-11-14 | Request checks: verified + **eligible** receiver with a profile; listing active with time and quantity left; quantity ≤ remaining; whole numbers for countable units; ≤ the receiver's capacity for that category **when the capacity uses the same unit**; preferred time inside the pickup window | Spec 5.3 "system validates the request"; plan issue H2 |
| D57 | 2026-11-14 | One active request per receiver per listing (pre-check + partial unique index); a new one is allowed after cancelling or rejection | Spec 8.4 "duplicate requests should be prevented"; the index also wins when two taps arrive at once |
| D58 | 2026-11-14 | The provider sees the receiver's address, phone and contact name only on requests for their own listing | Plan 5.3: contact details only within an authorized transaction |
| D59 | 2026-11-14 | Closing/removing a listing, or the expiry scan, turns its pending requests into `expired` (with history); a listing that has any requests can't be removed, only closed | Receivers aren't left waiting; requests are history (FR25) |
| D60 | 2026-11-14 | Rejections need a reason (≥ 5 characters), shown to the receiver; an admin's rejection is audited | Spec 4.1 "reject requests with a reason"; spec 4.4.6 admin intervention |
| D61 | 2026-11-14 | `purpose` is optional for all receivers | Spec 4.2.3 says "if required"; no rule makes it required yet |

| D62 | 2026-11-14 | Stage 5 split: 5a allocation first, then 5b matching | Allocation is the spec's core example; matching's fairness factor needs allocation history |
| D63 | 2026-11-14 | Allocation uses exact pure-Python algorithms (`app/engines/allocation.py`): greedy for priority-weighted and first-come, largest-remainder rounding for proportional-fair; OR-Tools CP-SAT is **not** used | With only "total ≤ available" and per-request caps, greedy is provably optimal (plan issue M10). Correctness is shown with property-based tests (Hypothesis, 9,000 random cases). A CP-SAT comparison belongs in the AI-evaluation stage |
| D64 | 2026-11-14 | Request caps = min(requested, the receiver's capacity in the same unit); ineligible, suspended or unverified receivers get 0 | Spec 6.4 constraints |
| D65 | 2026-11-14 | Default policy is priority-weighted; ties go to the earlier request | Plan section 12, decision 4; reproduces spec 5.5 exactly (15 / 10 / 5) |
| D66 | 2026-11-14 | Every suggestion (run) stores a JSONB snapshot of all inputs; only the newest draft can be confirmed; a confirm re-checks the listing version, the remaining quantity and every request's status under a row lock, otherwise 409 `RUN_STALE` | Reproducibility (plan 3.3) and safety under concurrent changes (plan issue H5) |
| D67 | 2026-11-14 | Changing a suggested quantity needs a written reason (≥ 5 characters); confirmations by admins or with overrides go to the audit log | Spec 4.4.7: "override recommendations with a recorded reason" |
| D68 | 2026-11-14 | Requests that end up with 0 are rejected with the line's explanation; a listing with nothing left becomes `fully_allocated`; cancelling an allocation returns the quantity and reactivates the listing (if its deadline hasn't passed) | Receivers always get an answer; food is never lost |
| D69 | 2026-11-14 | Over-allocation is blocked three times: the engine, the confirm check, and the database CHECK (allocated ≤ total), which is mapped to 409 `ALLOCATION_CONFLICT` | Defence in depth; tested by switching each layer off |
| D70 | 2026-11-14 | Every `SELECT … FOR UPDATE` names its table (`with_for_update(of=Model)`), enforced by a guard test | Locking a model that auto-joins optional rows fails in PostgreSQL; this caused two bugs |
| D71 | 2026-11-14 | No minimum allocation per receiver yet | Plan mentions an optional `min_alloc` setting; it needs `platform_settings` (admin stage) and CP-SAT |

| D72 | 2026-11-14 | Matching v1 is an explainable weighted heuristic (`app/engines/matching.py`, version label `heuristic-v1`), not ML | Plan 7.4 and 1.4: honest labelling; a learned ranker (v2) replaces it only if it measurably beats v1 |
| D73 | 2026-11-14 | Hard filters: verified + active + eligible; accepts the food category (declared preference); within the pickup radius, or with own transport within 50 km; not already allocated from this listing. Every exclusion is returned with its reason | Spec 6.3 "Receiver C: not eligible" + "displays recommendations and reasons" |
| D74 | 2026-11-14 | Score = 0.30 distance (exp(-km/5)) + 0.20 capacity fit + 0.15 collection fit (own transport 1.0, else 0.5) + 0.15 urgency feasibility + 0.10 category priority + 0.10 fairness (allocations in the last 30 days) | Plan 7.4 default weights. "Collection fit" uses transport as an honest proxy because receivers' collection hours aren't collected yet |
| D75 | 2026-11-14 | Matching results (live) show receiver name, category, distance and transport only; phone and address only appear once the receiver requests | Plan 5.3 field-level privacy |
| D76 | 2026-11-14 | A snapshot is stored in `match_recommendations` on publish and on refresh, keeping outcomes (`requested`; `completed` in the collection stage) | Weak labels and evaluation data for the ML ranker (plan 7.1) |
| D77 | 2026-11-14 | "Recommended for you" scores available listings for one receiver; fairness is neutral there (it compares receivers with each other); already-requested food is left out | Plan issue H6, job 2 |
| D78 | 2026-11-14 | Notifying the top-K matched receivers (`POST /listings/{id}/matches/notify`) is deferred to the notifications stage | Needs the notifications table and dispatcher |

| D79 | 2026-11-14 | Stage 6 split: 6a collections, 6b volunteer assignments | Keeps each stage small |
| D80 | 2026-11-14 | One collection per allocation, created when the allocation is confirmed; method from the request's preference; works without any volunteer | Plan issue M9 (the spec schema required an assignment) |
| D81 | 2026-11-14 | Quantities kept separately: approved (allocation) → handed over (provider confirms, ≤ approved) → received (receiver confirms, ≤ handed over; DB CHECK) | Spec 14.3: listed, allocated and collected/delivered must not be interchangeable (plan 3.5) |
| D82 | 2026-11-14 | Receipt completes the redistribution: request `completed`, allocation `completed`, match outcome `completed` | Spec 5.7 "Receiver Confirms Quantity → Redistribution Completed"; the outcome is an ML label |
| D83 | 2026-11-14 | Only the provider (or an admin) confirms handover; only the receiver (or an admin **with a reason**, audited) confirms receipt | Plan 5.4 permission matrix |
| D84 | 2026-11-14 | A failed collection: the request is cancelled with the reason; if the food hadn't left the provider, its quantity returns to the listing (which reactivates if its deadline hasn't passed); always audited | Food is never silently lost |
| D85 | 2026-11-14 | Provider and receiver contact details (phone, address, contact person) are shown to both sides on the collection | Spec 15: contact details shared within an authorized transaction |
| D86 | 2026-11-14 | An allocation can only be cancelled before the handover; afterwards, report a failed collection | The food has physically left the provider |
| D87 | 2026-11-14 | A general "report a problem" (issue reports, plan 4.8) is deferred to the admin stage; collection problems use "fail" with a reason | Issues need the admin review screens |

| D88 | 2026-11-14 | A collection with method "volunteer" gets an OPEN delivery task automatically (on allocation, or when the method is switched); switching back to self pickup cancels it | Spec 5.6 "provider or receiver requests collection support" |
| D89 | 2026-11-14 | At most one active task per collection (partial unique index); pickup and drop-off points are snapshots | Plan 3.3; a profile edit can't move a delivery already on its way |
| D90 | 2026-11-14 | Volunteers claim open tasks inside their service radius (claim = accept), or accept/decline tasks an admin assigned; a decline needs a reason and re-opens the task | Spec 4.3.3 "accept / reject an assignment" |
| D91 | 2026-11-14 | Delivery steps follow a state machine: accepted → en route to pickup → collected → en route to receiver → delivered → completed (by the receiver's receipt); "failed" needs a reason | Spec 4.3.4 status list |
| D92 | 2026-11-14 | The volunteer's "collected" step IS the collection handover (same rules, shared code); the provider confirming a handover moves the task to collected too | One source of truth for the handed-over quantity |
| D93 | 2026-11-14 | Before accepting, volunteers see the pickup point and distances only; the receiver's address and both phone numbers appear after accepting and disappear 7 days after completion | Plan 5.3 field-level privacy |
| D94 | 2026-11-14 | Suggested volunteers: heuristic (plan 7.6), score = 0.35 distance + 0.25 availability (weekly slot at the pickup time, in Pakistan time UTC+5) + 0.15 vehicle (matters for loads > 20) + 0.15 workload + 0.10 reliability (0.8 for newcomers); outside the service radius = excluded with a reason | Explainable; admins decide |
| D95 | 2026-11-14 | Admins can assign, reassign (only before the pickup starts: the old task is cancelled and a new one created) or cancel (the collection becomes a self pickup); all audited | Spec 4.4.8 "assign volunteers, change assignments" |
| D96 | 2026-11-14 | No live GPS tracking of volunteers | Plan 1.5 out of scope: status updates only |

## Known evaluation item

- With the default weights, a receiver 33 km away **with a van and enough capacity** scored above one 5.9 km away without transport (0.68 vs 0.58), because exp(-km/5) falls steeply. Behaving as designed, but possibly not what people expect. Check against the hand-labelled evaluation set (plan 7.4) in the AI stage and tune the weights / distance curve there.

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
