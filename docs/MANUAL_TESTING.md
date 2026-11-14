# FreshLink — Manual Testing Guide (Stages 0–6b)

This guide tests **every API built so far (94 endpoints)** by hand in Swagger UI.
Each step says exactly what to type and what you should see.

- **Time needed:** about 165 minutes
- **You need:** any small real PDF, JPG or PNG file on your computer (for section 10.4)
- **Tick each box** as you go. If a result differs, copy the response and send it to the team.
- All test accounts use emails starting with `guide.` so they're easy to delete afterwards (section 11).

---

## 0. Start everything

1. **Start Docker Desktop** (Start menu → Docker Desktop). Wait until it says **Engine running**.
   *Without Docker, the database is off, and every test fails with "Cannot connect to PostgreSQL".*
2. Open **PowerShell** and run these one at a time:

```powershell
cd D:\University\FreshLink
docker compose up -d
cd backend
.\.venv\Scripts\Activate.ps1
alembic upgrade head
uvicorn app.main:app --reload
```

3. Leave that window open. When it shows `Application startup complete`, open **http://127.0.0.1:8000/docs** in Chrome.

### How to use Swagger (read once)
- Click an endpoint row to open it → **Try it out** → edit the **Request body** → **Execute**.
- The result is under **Server response**: look at the **Code** and the **Response body**.
- The **Responses** section below that is only documentation, not your result.
- **Authorize** (top right): paste an `access_token` there to act as that user. To switch users, open Authorize → **Logout** → paste the other token.
- Access tokens expire after **15 minutes**. If you see `TOKEN_EXPIRED`, log in again (step 3.4) and re-authorize.

---

## 1. System (2 endpoints)

| ✓ | Endpoint | Do | Expected |
|---|---|---|---|
| ☐ | `GET /api/v1/health` | Execute | **200** `{"status": "ok", "database": "ok"}` |
| ☐ | `GET /api/v1/version` | Execute | **200** `{"name": "FreshLink API", "version": "0.1.0", "environment": "development"}` |

---

## 2. Lookups: public, no login (4 endpoints)

| ✓ | Endpoint | Expected |
|---|---|---|
| ☐ | `GET /api/v1/lookups/provider-categories` | **200**, 4 items: Bakery, Restaurant, Fast-food shop, Grocery store / Supermarket |
| ☐ | `GET /api/v1/lookups/receiver-categories` | **200**, 5 items: Welfare organization / NGO, Community kitchen, Community group, Individual / household, Small food business |
| ☐ | `GET /api/v1/lookups/food-categories` | **200**, 7 items. "Cooked meals" has `requires_preparation_time: true`; "Dairy products" has `requires_expiry_date: true` |
| ☐ | `GET /api/v1/lookups/enums` | **200**. `public_roles` is `["provider", "receiver", "volunteer"]` (no admin) |

📋 **Copy these IDs into Notepad. You'll need them later:**
- the `id` of **Bakery** (provider categories),
- the `id` of **Community kitchen** and of **Small food business** (receiver categories),
- the `id` of **Bakery items** and of **Cooked meals** (food categories).

---

## 3. Registration and login

### 3.1 Register three users — `POST /api/v1/auth/register`

Execute three times, once with each body. **Expected each time: 201**, `is_verified: false`, phone shown as `+92…`.

```json
{ "email": "guide.provider@example.com", "phone": "0300 1110001", "full_name": "Guide Provider", "password": "Fresh-Link-2026", "role": "provider" }
```
```json
{ "email": "guide.receiver@example.com", "phone": "0300 1110002", "full_name": "Guide Receiver", "password": "Fresh-Link-2026", "role": "receiver" }
```
```json
{ "email": "guide.volunteer@example.com", "phone": "0300 1110003", "full_name": "Guide Volunteer", "password": "Fresh-Link-2026", "role": "volunteer" }
```
☐ provider 201 ☐ receiver 201 ☐ volunteer 201

### 3.2 Registration errors (same endpoint)

| ✓ | Change in the body (start from the provider body) | Expected |
|---|---|---|
| ☐ | Send it again unchanged | **409** `EMAIL_ALREADY_REGISTERED` |
| ☐ | `"email": "GUIDE.PROVIDER@example.com"` (capitals) | **409** `EMAIL_ALREADY_REGISTERED` (emails ignore case) |
| ☐ | new email `guide.x@example.com`, `"phone": "+92 300 1110001"` | **409** `PHONE_ALREADY_REGISTERED` (same number, different format) |
| ☐ | new email and phone, `"role": "admin"` | **422** `VALIDATION_ERROR`, field `body.role` |
| ☐ | new email and phone, `"password": "short"` | **422**, message *Password must be at least 8 characters long.* |
| ☐ | new email and phone, `"password": "password123"` | **422**, message *This password is too common…* |
| ☐ | new email, `"phone": "12345"` | **422**, field `body.phone` |
| ☐ | `"email": "not-an-email"` | **422**, field `body.email` |
| ☐ | Body `{}` | **422**, 5 fields listed |

### 3.3 Login errors — `POST /api/v1/auth/login`

| ✓ | Body | Expected |
|---|---|---|
| ☐ | `{"identifier": "guide.provider@example.com", "password": "Wrong-Pass-11"}` | **401** `INVALID_CREDENTIALS` |
| ☐ | `{"identifier": "nobody@example.com", "password": "Wrong-Pass-11"}` | **401**, exactly the same message as above (doesn't reveal who is registered) |

### 3.4 Log in as the provider (mobile style)

```json
{ "identifier": "0300-1110001", "password": "Fresh-Link-2026", "platform": "mobile" }
```
☐ **200**: you get an `access_token` **and** a `refresh_token`. The phone matched even though it's typed differently.

📋 Copy the `refresh_token` into Notepad as **R1**.
🔑 Copy the `access_token` → **Authorize** → paste → Authorize → Close.

### 3.5 Who am I? — `GET /api/v1/auth/me` and `GET /api/v1/users/me`
☐ both **200**, showing Guide Provider with `"role": "provider"` and `"last_login_at"` filled in.

---

## 4. Provider profile (2 endpoints, logged in as provider)

| ✓ | Step | Expected |
|---|---|---|
| ☐ | `GET /api/v1/providers/me` | **404** `PROFILE_NOT_FOUND` (no profile yet; this is correct) |
| ☐ | `PUT /api/v1/providers/me` with the body below (paste your Bakery id) | **200**, `business_name` is "Guide Fresh Bakers", `category.name` is "Bakery", `contact_phone` is "+923001110001" |
| ☐ | `GET /api/v1/providers/me` again | **200**, the same profile |
| ☐ | `PUT` again with `"latitude": 95` | **422**, field `body.latitude` |
| ☐ | `PUT` again with `"provider_category_id": "00000000-0000-0000-0000-000000000000"` | **422** `CATEGORY_NOT_AVAILABLE` |
| ☐ | `PUT` again with `"opening_hours": {"mon": [{"open": "10:00", "close": "09:00"}]}` | **422**, *Closing time must be after opening time.* |

```json
{
  "business_name": "Guide Fresh Bakers",
  "provider_category_id": "PASTE-BAKERY-ID",
  "address": "Main Boulevard, Gulberg III, Lahore",
  "latitude": 31.5204,
  "longitude": 74.3587,
  "contact_person": "Guide Provider",
  "contact_phone": "0300 1110001",
  "opening_hours": { "mon": [ { "open": "09:00", "close": "22:00" } ] }
}
```

### 4.1 Permissions (still logged in as provider)
| ✓ | Endpoint | Expected |
|---|---|---|
| ☐ | `GET /api/v1/receivers/me` | **403** `FORBIDDEN` (a provider can't use receiver endpoints) |
| ☐ | `GET /api/v1/volunteers/me` | **403** `FORBIDDEN` |

---

## 5. Sessions: refresh, reuse, logout (provider)

| ✓ | Endpoint and body | Expected |
|---|---|---|
| ☐ | `POST /api/v1/auth/refresh` `{"refresh_token": "R1"}` | **200**, new `access_token` and a **different** `refresh_token`. 📋 save it as **R2** |
| ☐ | Same request again with **R1** (the old one) | **401** `REFRESH_TOKEN_REUSED`: someone reused an old token, so the session is ended |
| ☐ | `POST /api/v1/auth/refresh` with **R2** | **401** (R2 also died; the thief gains nothing) |
| ☐ | `POST /api/v1/auth/refresh` `{"refresh_token": "made-up"}` | **401** `INVALID_REFRESH_TOKEN` |

Now log in again as the provider (step 3.4), save the new refresh token as **R3**, and re-authorize with the new access token.

| ✓ | Endpoint and body | Expected |
|---|---|---|
| ☐ | `POST /api/v1/auth/logout` `{"refresh_token": "R3"}` | **204** (no body) |
| ☐ | `POST /api/v1/auth/refresh` with **R3** | **401**: that device is logged out |
| ☐ | `POST /api/v1/auth/logout` with body `{}` | **204**: logout is safe to repeat |

---

## 6. Receiver profile and capacity (4 endpoints)

Log in as the receiver (`0300-1110002`, `"platform": "mobile"`) and **Authorize** with its access token.

| ✓ | Step | Expected |
|---|---|---|
| ☐ | `GET /api/v1/receivers/me` | **404** `PROFILE_NOT_FOUND` |
| ☐ | `PUT /api/v1/receivers/me/food-preferences` `{"preferences": []}` | **409** `PROFILE_REQUIRED` (profile first) |
| ☐ | `PUT /api/v1/receivers/me` with the body below (Community kitchen id) | **200**, `category.name` is "Community kitchen", `is_eligible` is true, `food_preferences` is [] |
| ☐ | Same, but with the **Small food business** id | **422** `NOT_FOR_RESALE_REQUIRED` |
| ☐ | Small food business id **plus** `"not_for_resale_declared": true` | **200** |
| ☐ | Put the Community kitchen id back, plus `"max_pickup_radius_km": 5.25` | **422** (only 1 decimal place allowed) |
| ☐ | Community kitchen id again (original body) | **200** |

```json
{
  "receiver_name": "Guide Community Kitchen",
  "receiver_category_id": "PASTE-COMMUNITY-KITCHEN-ID",
  "address": "Model Town, Lahore",
  "latitude": 31.48,
  "longitude": 74.32,
  "max_pickup_radius_km": 6,
  "has_own_transport": false
}
```

### 6.1 Capacity per food category — `PUT /api/v1/receivers/me/food-preferences`

```json
{
  "preferences": [
    { "food_category_id": "PASTE-BAKERY-ITEMS-ID", "max_quantity_per_pickup": 20, "unit": "package" },
    { "food_category_id": "PASTE-COOKED-MEALS-ID", "max_quantity_per_pickup": 12.5, "unit": "kg" }
  ]
}
```
| ✓ | Step | Expected |
|---|---|---|
| ☐ | Execute the body above | **200**, a list with both categories |
| ☐ | `GET /api/v1/receivers/me/food-preferences` | **200**, the same 2 items |
| ☐ | Change the package line to `"max_quantity_per_pickup": 2.5` | **422**, *must be a whole number* (packages can't be split) |
| ☐ | Use the same category id on both lines | **422**, *Each food category can only appear once.* |
| ☐ | Send `{"preferences": []}` | **200** `[]` (list cleared) |

---

## 7. Volunteer profile and availability (4 endpoints)

Log in as the volunteer (`0300-1110003`, `"platform": "mobile"`) and **Authorize**.

| ✓ | Step | Expected |
|---|---|---|
| ☐ | `GET /api/v1/volunteers/me` | **404** `PROFILE_NOT_FOUND` |
| ☐ | `PUT /api/v1/volunteers/me/availability` `{"slots": []}` | **409** `PROFILE_REQUIRED` |
| ☐ | `PUT /api/v1/volunteers/me` with the body below | **200**, `vehicle_type` "motorbike", `availability` [] |
| ☐ | Same body with `"has_vehicle": false` | **422** (a vehicle type needs a vehicle) |
| ☐ | `PUT /api/v1/volunteers/me/availability` with the slots below | **200**, slots returned in week order (Monday first) |
| ☐ | `GET /api/v1/volunteers/me/availability` | **200**, the same 2 slots |
| ☐ | Slots `[{"day_of_week": 1, "start_time": "09:00", "end_time": "12:00"}, {"day_of_week": 1, "start_time": "11:00", "end_time": "13:00"}]` | **422**, *Time slots overlap on tue.* |

```json
{ "address": "Johar Town, Lahore", "latitude": 31.47, "longitude": 74.27,
  "has_vehicle": true, "vehicle_type": "motorbike", "service_radius_km": 8, "is_available_now": true }
```
```json
{ "slots": [
    { "day_of_week": 4, "start_time": "17:00", "end_time": "21:00" },
    { "day_of_week": 0, "start_time": "09:00", "end_time": "12:00" } ] }
```

---

## 8. Account update (volunteer still logged in) — `PUT /api/v1/users/me`

| ✓ | Body | Expected |
|---|---|---|
| ☐ | `{"full_name": "  Guide   Volunteer Two ", "phone": "0300 1110003"}` | **200**, name cleaned to "Guide Volunteer Two" |
| ☐ | `{"full_name": "Guide Volunteer", "phone": "0300-1110001"}` (the provider's number) | **409** `PHONE_ALREADY_REGISTERED` |

---

## 9. Change password and log out everywhere (volunteer)

| ✓ | Step | Expected |
|---|---|---|
| ☐ | `POST /api/v1/auth/change-password` `{"current_password": "Wrong-Pass-11", "new_password": "New-Fresh-2026"}` | **400** `WRONG_CURRENT_PASSWORD` |
| ☐ | `{"current_password": "Fresh-Link-2026", "new_password": "Fresh-Link-2026"}` | **400** `PASSWORD_UNCHANGED` |
| ☐ | `{"current_password": "Fresh-Link-2026", "new_password": "New-Fresh-2026", "platform": "mobile"}` | **200**, new tokens |
| ☐ | `GET /api/v1/auth/me` (still using the OLD token in Authorize) | **401** `TOKEN_REVOKED`: old sessions end on a password change |
| ☐ | Authorize with the NEW access token → `GET /api/v1/auth/me` | **200** |
| ☐ | Log in with the old password | **401** `INVALID_CREDENTIALS` |
| ☐ | `POST /api/v1/auth/logout-all` (new token authorized) | **204** |
| ☐ | `GET /api/v1/auth/me` | **401** `TOKEN_REVOKED` (logged out on every device) |
| ☐ | Authorize → **Logout** in the dialog → `GET /api/v1/auth/me` with no token | **401** `UNAUTHENTICATED` |

---

## 10. Security checks

### 10.1 Brute-force protection — `POST /api/v1/auth/login`
Limits: **5 failed logins per account** and **20 per IP address**, per minute. (The IP limit is higher because many mobile users in Pakistan share one IP.) This step blocks the receiver account for 1 minute.

| ✓ | Step | Expected |
|---|---|---|
| ☐ | `{"identifier": "guide.receiver@example.com", "password": "Bad-Guess-01"}` five times | **401** each time |
| ☐ | The 6th time | **429** `TOO_MANY_LOGIN_ATTEMPTS`, *try again in N seconds* |
| ☐ | Immediately with the **correct** password `Fresh-Link-2026` | **429** as well (a guesser can't tell when they're right) |
| ☐ | Wait 60 seconds, then log in with the correct password | **200** |

### 10.2 First administrator (command line)
In a **second** PowerShell window:
```powershell
cd D:\University\FreshLink\backend
.\.venv\Scripts\Activate.ps1
python -m app.cli create-admin
```
Answer: email `guide.admin@example.com`, name `Guide Admin`, phone `0300 1110009`, password `Admin-Fresh-2026` (typed twice; it stays hidden).

| ✓ | Step | Expected |
|---|---|---|
| ☐ | The command | `[OK] Super admin created: guide.admin@example.com` |
| ☐ | Typing `password123` as the password | `[x] This password is too common…`, then asked again |
| ☐ | Swagger login as `guide.admin@example.com` | **200**, `"role": "admin"`, `"is_super_admin": true`, `"is_verified": true` |
| ☐ | Authorize as admin → `GET /api/v1/providers/me` | **403** (admins have no provider profile) |

### 10.3 Database down
| ✓ | Step | Expected |
|---|---|---|
| ☐ | In a second PowerShell: `cd D:\University\FreshLink` then `docker compose stop` | — |
| ☐ | `GET /api/v1/health` | **503** `{"status": "degraded", "database": "unavailable"}` (wait up to 5 s) |
| ☐ | `POST /api/v1/auth/login` with any body | **503** `DATABASE_UNAVAILABLE`, a clean message with no technical details |
| ☐ | `docker compose start`, wait 10 s, `GET /api/v1/health` | **200** again, with no API restart needed |


### 10.4 Account verification: documents and admin review (Stage 2b)

**Part A: the provider submits documents.** Log in as the provider (`0300-1110001`, password `Fresh-Link-2026`, `"platform": "mobile"`) and **Authorize** with that token.

| ✓ | Step | Expected |
|---|---|---|
| ☐ | `GET /api/v1/lookups/verification-requirements`, `role` = `provider` | **200**: "Proof of business" (required) and "Photo of the shop front" (optional) |
| ☐ | `GET /api/v1/me/verification` | **200**, `verification_status` "not_submitted", `profile_completed` true, `can_submit` true |
| ☐ | `POST /api/v1/files`: **Choose File** (your PDF/JPG/PNG), `purpose` = `verification_doc` | **201**. 📋 copy the `id` as **FILE1** |
| ☐ | Same, but choose a `.txt` file renamed to `.pdf` | **415** `UNSUPPORTED_FILE_TYPE` (the content is checked, not the name) |
| ☐ | `GET /api/v1/files/{file_id}` with FILE1 | **200**, your file opens/downloads |
| ☐ | `POST /api/v1/me/verification` with `{"documents": []}` | **422** `MISSING_REQUIRED_DOCUMENTS`, *Please upload: Proof of business.* |
| ☐ | `POST /api/v1/me/verification` with the body below | **201**, `"status": "pending"` |
| ☐ | Send it again | **409** `VERIFICATION_ALREADY_PENDING` |

```json
{ "documents": [ { "file_id": "PASTE-FILE1", "document_type": "business_proof" } ], "note": "Food authority licence attached" }
```

**Part B: another user can't see the document.** Log in as the volunteer (password is now `New-Fresh-2026`) and Authorize.

| ✓ | Step | Expected |
|---|---|---|
| ☐ | `GET /api/v1/files/{file_id}` with FILE1 | **404** (strangers can't even learn the file exists) |
| ☐ | `GET /api/v1/admin/verifications` | **403** (admins only) |

**Part C: the admin reviews.** Log in as `guide.admin@example.com` / `Admin-Fresh-2026` and Authorize.

| ✓ | Step | Expected |
|---|---|---|
| ☐ | `GET /api/v1/admin/verifications` | **200**, `total` 1: Guide Provider, role provider, `document_count` 1. 📋 copy the item `id` as **REQ1** |
| ☐ | `GET /api/v1/admin/verifications/{request_id}` with REQ1 | **200**, includes `provider_profile` (Guide Fresh Bakers) and the document |
| ☐ | `GET /api/v1/files/{file_id}` with FILE1 | **200**: the admin can open the document |
| ☐ | `POST .../{request_id}/request-info` with `{"reason": "no"}` | **422** (a clear reason is required) |
| ☐ | `POST .../{request_id}/request-info` with `{"reason": "Please upload a clearer photo of the licence."}` | **200**, `"status": "info_requested"` |
| ☐ | `POST .../{request_id}/approve` with `{}` | **409** `ALREADY_REVIEWED` (only pending requests can be decided) |

**Part D: the provider resubmits, the admin approves.**

| ✓ | Step | Expected |
|---|---|---|
| ☐ | As the provider: `GET /api/v1/me/verification` | `"info_requested"`, `review_notes` shows the admin's message, `can_submit` true |
| ☐ | As the provider: upload again (FILE2) and submit it as `business_proof` | **201** pending |
| ☐ | As the admin: `GET /api/v1/admin/verifications` → copy the new `id` (REQ2) → `POST .../{REQ2}/approve` with `{"notes": "Licence checked"}` | **200** `"approved"` |
| ☐ | As the admin: `GET /api/v1/admin/verifications/{REQ2}` | `history` lists the earlier request as `info_requested` |
| ☐ | As the provider: `GET /api/v1/auth/me` | `"verification_status": "approved"`, `"is_verified": true` |

**Part E: receiver eligibility (admin).** You need the receiver's user `id`: log in as the receiver, call `GET /api/v1/auth/me` and copy `id`, then Authorize as admin again.

| ✓ | Step | Expected |
|---|---|---|
| ☐ | `PATCH /api/v1/admin/receivers/{user_id}/eligibility` with `{"is_eligible": false}` | **422** `REASON_REQUIRED` |
| ☐ | Same with `{"is_eligible": false, "notes": "Duplicate organization"}` | **200**, `"is_eligible": false` |
| ☐ | Same with `{"is_eligible": true}` | **200**, eligible again |

**Part F: the audit log.** In PowerShell:
```powershell
docker exec freshlink-db psql -U freshlink -d freshlink -c "SELECT action, actor_email, reason FROM audit_logs ORDER BY created_at;"
```
☐ Four rows: `verification.request_info`, `verification.approve`, and two `receiver.eligibility`, each with the admin's email.
☐ Try to tamper with it: `docker exec freshlink-db psql -U freshlink -d freshlink -c "DELETE FROM audit_logs;"` → **ERROR: audit_logs is append-only**.


### 10.5 Food listings (Stage 3a)

Log in as the provider (`0300-1110001` / `Fresh-Link-2026`, `"platform": "mobile"`), who was approved in 10.4, and **Authorize**.
You also need the food category ids from section 2 (**Bakery items** and **Cooked meals**).

⏰ **About times:** every time must include `+05:00` (Pakistan time). Use times from **today**, a little after the current time.
Example: if it's now 15 Nov, 14:00 → `"pickup_window_start": "2026-11-15T15:00:00+05:00"`, `"pickup_window_end": "2026-11-15T18:00:00+05:00"`, and a preparation time of `"2026-11-15T13:00:00+05:00"`.

| ✓ | Step | Expected |
|---|---|---|
| ☐ | `POST /api/v1/listings` with the body below, but **without** the `preparation_at` line | **422** `PREPARATION_TIME_REQUIRED` (cooked meals need it) |
| ☐ | Same, with `"preparation_at"` set 2 hours in the **future** | **422** `DATE_IN_FUTURE` |
| ☐ | Same, with `"pickup_window_start": "2026-11-15T15:00:00"` (no `+05:00`) | **422**, *should have timezone info* |
| ☐ | Same, with `"declaration_accepted": false` | **422**, *Please confirm the declaration…* |
| ☐ | The body below, unchanged | **201**: `status` active, `version` 1, `remaining_quantity` 12.5, `pickup_address` is the business address. 📋 copy the `id` as **L1** |
| ☐ | `GET /api/v1/listings/{listing_id}` with L1 | **200**; the provider card shows only the business name (no phone) |
| ☐ | `GET /api/v1/providers/me/listings` | **200**, `total` 1 |
| ☐ | `PUT /api/v1/listings/{listing_id}` with the same body but `"total_quantity": 15`, **If-Match empty** | **428** `VERSION_REQUIRED` |
| ☐ | Same with **If-Match** = `1` | **200**, `version` 2, `total_quantity` 15 |
| ☐ | Same again with **If-Match** = `1` (an old version, like a second device) | **409** `STALE_VERSION`, `current_version` 2 |
| ☐ | `POST /api/v1/listings/{listing_id}/pause` with `{"reason": "Running late"}` | **200** `paused` |
| ☐ | As a **verified receiver** you'd now get 404 for this listing (paused = hidden). Optional check with the receiver token. | **404** |
| ☐ | `POST .../resume` | **200** `active` |
| ☐ | `POST .../close` | **200** `closed` |
| ☐ | `POST .../resume` again | **409** `INVALID_STATUS_TRANSITION` (closed is final) |
| ☐ | `GET /api/v1/listings/{listing_id}/history` | 4 rows: created → paused (Running late) → active → closed |
| ☐ | Create a second listing (body below), then `DELETE /api/v1/listings/{listing_id}` on it | **204**, then GET it → **404** |
| ☐ | **Food photo:** `POST /api/v1/files` with a JPG/PNG and `purpose` = `listing_image` → put its `id` in `"image_file_id"` when creating a listing | **201**, the listing shows `image` |

```json
{
  "food_category_id": "PASTE-COOKED-MEALS-ID",
  "food_name": "Chicken biryani",
  "unit": "kg",
  "total_quantity": 12.5,
  "preparation_at": "2026-11-15T13:00:00+05:00",
  "pickup_window_start": "2026-11-15T15:00:00+05:00",
  "pickup_window_end": "2026-11-15T18:00:00+05:00",
  "storage_info": "Keep covered",
  "declaration_accepted": true
}
```

💡 The response shows times in UTC (`...Z`), e.g. 18:00+05:00 appears as 13:00Z. It's the same moment; the apps will display Pakistan time.


### 10.6 Urgency (Stage 3b)

Still logged in as the provider. Create a new **Cooked meals** listing like in 10.5, with
`preparation_at` **9 hours ago** and the pickup window ending **8 hours** from now.

| ✓ | Step | Expected |
|---|---|---|
| ☐ | Look at `urgency` in the 201 response | `level` **high**, explanation *High urgency: preparation time limit (12 h) in about 3 h* (cooked food counts from preparation, not from the pickup time), and the disclaimer *…not a food-safety assessment.* 📋 copy the `id` as **L2** |
| ☐ | `GET /api/v1/listings/{listing_id}/urgency` with L2 | the same urgency on its own, including the `rule` (12 / 6 / 2 / 12) |
| ☐ | Create a **Bakery items** listing whose pickup window ends 3 days from now | `level` **normal** |

**Admin: change a rule.** Authorize as `guide.admin@example.com`.

| ✓ | Step | Expected |
|---|---|---|
| ☐ | `GET /api/v1/admin/urgency-rules` | 7 rules, each with the note *Not a food-safety limit* |
| ☐ | `PUT /api/v1/admin/urgency-rules/{food_category_id}` (Cooked meals id) with `{"medium_hours": 12, "high_hours": 2, "critical_hours": 6, "max_hours_since_preparation": 12}` | **422**, *critical < high < medium* |
| ☐ | Same with `{"medium_hours": 12, "high_hours": 6, "critical_hours": 4, "max_hours_since_preparation": 12}` | **200** |
| ☐ | Authorize as the provider → `GET /api/v1/listings/{listing_id}/urgency` with L2 | now **critical** (3 h left ≤ the new 4 h limit). The new rule applies immediately |
| ☐ | Put the rule back: `critical_hours` 2 | **200** |

**Automatic expiry.** In PowerShell, make L2's deadline pass, then run the scan:
```powershell
docker exec freshlink-db psql -U freshlink -d freshlink -c "UPDATE food_listings SET pickup_window_start = now() - interval '5 hours', pickup_window_end = now() - interval '2 hours', collection_deadline = now() - interval '1 hour' WHERE id = 'PASTE-L2';"
cd D:\University\FreshLink\backend
.\.venv\Scripts\Activate.ps1
python -m app.cli urgency-scan
```
| ✓ | Step | Expected |
|---|---|---|
| ☐ | The scan command | `[OK] Checked … open listings: 1 expired, …` |
| ☐ | `GET /api/v1/listings/{listing_id}` with L2 | `"status": "expired"` |
| ☐ | `GET /api/v1/listings/{listing_id}/history` with L2 | last row `expired`, reason *Deadline passed: the collection deadline was 1 h ago.* |
| ☐ | Run `python -m app.cli urgency-scan` again | `0 expired` (safe to run any number of times) |


### 10.7 Browse and search food (Stage 4a)

First make the **receiver** verified (receivers can only browse after approval). As **admin**, approve the receiver
the same way as the provider in 10.4. Or, for a quick test, run this in PowerShell:
```powershell
docker exec freshlink-db psql -U freshlink -d freshlink -c "UPDATE users SET verification_status = 'approved' WHERE email = 'guide.receiver@example.com';"
```
Then, as the **provider**, create two listings (as in 10.5) with their own pickup location:
- "Fresh naan" (Bakery items, `package`, 40), pickup window ending in 30 h, `"pickup_address": "Gulberg"`, `"pickup_latitude": 31.521`, `"pickup_longitude": 74.359`
- "Cake slices" (Bakery items, `piece`, 25), `"pickup_address": "Raiwind"`, `"pickup_latitude": 31.25`, `"pickup_longitude": 74.21`

Log in as the receiver (`0300-1110002`, password `Fresh-Link-2026`; wait a minute if 10.1 blocked it) and **Authorize**.
The receiver's saved location (section 6) is Model Town with a 6 km radius.

| ✓ | `GET /api/v1/listings` with… | Expected |
|---|---|---|
| ☐ | nothing (defaults) | **200**. `location.source` is `"profile"`, `radius_km` 6. Naan (~5.9 km) is listed with `distance_km`; the cake (Raiwind, ~27.6 km) is **not** |
| ☐ | `radius_km` = 50, `sort` = distance | both, nearest first |
| ☐ | `q` = `nann` (a typo) | still finds "Fresh naan" |
| ☐ | `unit` = package, `min_quantity` = 50 | none (only 40 left) |
| ☐ | `min_quantity` = 10 without `unit` | **422** `UNIT_REQUIRED` |
| ☐ | `urgency` = normal | only the listings whose level is normal |
| ☐ | `near_lat` = 31.25, `near_lng` = 74.21, `radius_km` = 5 | only the cake (`location.source` = `"request"`) |
| ☐ | As the provider: pause the naan, then browse again as the receiver | the naan disappears (paused food is hidden) |
| ☐ | `GET /api/v1/listings` with the **provider's** token | **403** (only receivers browse) |


### 10.8 Food requests (Stage 4b)

As the **provider**, create a fresh listing for the requests: "Bread packages" (Bakery items, `package`, `total_quantity` 30),
pickup window from 1 h to 5 h from now, at the business address. 📋 copy its `id` as **L3**.
As the **receiver** (verified in 10.7), set your capacity: `PUT /api/v1/receivers/me/food-preferences` with Bakery items, `max_quantity_per_pickup` 15, `unit` `package`.

| ✓ | As the receiver: `POST /api/v1/requests` with… | Expected |
|---|---|---|
| ☐ | `{"listing_id": "L3", "requested_quantity": 20}` | **422** `EXCEEDS_CAPACITY`, *…at most 15 packages of this food per pickup* |
| ☐ | `{"listing_id": "L3", "requested_quantity": 2.5}` | **422** `WHOLE_NUMBER_REQUIRED` (packages can't be split) |
| ☐ | `{"listing_id": "L3", "requested_quantity": 15, "purpose": "Evening meal", "collection_preference": "self_pickup"}` | **201** `pending`, with `distance_km`. 📋 copy `id` as **Q1** |
| ☐ | the same again | **409** `DUPLICATE_REQUEST` (one active request per listing) |

| ✓ | Step | Expected |
|---|---|---|
| ☐ | Receiver: `GET /api/v1/requests` | your request(s) only |
| ☐ | Receiver: `PATCH /api/v1/requests/{request_id}` (Q1) with `{"requested_quantity": 12, "purpose": "Evening meal"}` | **200**, `requested_quantity` 12, `version` 2 |
| ☐ | Provider: `GET /api/v1/listings/{listing_id}/requests` (L3) | the request, **with the receiver's phone and address** (allowed: they asked for your food) |
| ☐ | Provider: `POST /api/v1/requests/{request_id}/reject` (Q1) with `{"reason": "no"}` | **422** (a clear reason is required) |
| ☐ | Provider: same with `{"reason": "Please collect from our other branch tomorrow."}` | **200** `rejected` |
| ☐ | Receiver: `GET /api/v1/requests/{request_id}` (Q1) | `rejected`, with the provider's reason |
| ☐ | Receiver: request L3 again (quantity 10) | **201**: allowed after a rejection. 📋 copy as **Q2** |
| ☐ | Provider: `DELETE /api/v1/listings/{listing_id}` (L3) | **409** `LISTING_HAS_REQUESTS` (close it instead) |
| ☐ | Provider: `POST /api/v1/listings/{listing_id}/close` (L3) | **200** `closed` |
| ☐ | Receiver: `GET /api/v1/requests/{request_id}/history` (Q2) | `pending` → `expired`, reason *listing closed by the provider* |
| ☐ | Volunteer: `GET /api/v1/requests` | **403** (volunteers don't have requests) |


### 10.9 Allocation: the specification's example (Stage 5a)

Spec 5.5: *a bakery has 30 bread packages; an NGO asks for 15, a community kitchen for 10 and a family for 10.*

**Set up two more receivers.** Register them with `POST /api/v1/auth/register` (`"role": "receiver"`, password `Fresh-Link-2026`):
`guide.kitchen@example.com` (phone `0300 1110004`) and `guide.family@example.com` (phone `0300 1110005`). Approve both quickly:
```powershell
docker exec freshlink-db psql -U freshlink -d freshlink -c "UPDATE users SET verification_status = 'approved' WHERE email IN ('guide.kitchen@example.com', 'guide.family@example.com');"
```
Log in as each and create its profile with `PUT /api/v1/receivers/me`: the kitchen with the **Community kitchen** category,
the family with **Individual / household** plus `"household_size": 6`. Use the Model Town address from section 6.
Your existing `guide.receiver` is the NGO, but **change its category to Welfare organization / NGO** (`PUT /api/v1/receivers/me`)
and clear its capacity (`PUT /api/v1/receivers/me/food-preferences` with `{"preferences": []}`).

**The listing and the requests.** As the provider, create "Bread packages" (Bakery items, `package`, **30**), pickup from 1 h to 5 h from now
(📋 **L4**). Then request it, **in this order**: NGO **15**, kitchen **10**, family **10**.

| ✓ | Step | Expected |
|---|---|---|
| ☐ | Provider: `POST /api/v1/listings/{listing_id}/allocation-runs` (L4) with `{"policy": "priority_weighted"}` | **201** draft: NGO **15**, kitchen **10**, family **5**, exactly as in spec 5.5. The family's line explains *5 packages of 10 packages requested: the remaining quantity after higher-priority or earlier requests*. 📋 copy the run `id` as **RUN1** |
| ☐ | Same with `{"policy": "proportional_fair"}` | NGO **13**, kitchen **9**, family **8** (everyone gets about 86% of what they asked for). 📋 **RUN2** |
| ☐ | `POST /api/v1/allocation-runs/{run_id}/confirm` with RUN1 and `{"lines": []}` | **409** `RUN_NOT_DRAFT`: only the newest suggestion (RUN2) can be confirmed |
| ☐ | Create a priority suggestion again (📋 **RUN3**), then confirm it with `{"lines": [{"request_id": "FAMILY-REQUEST-ID", "approved_quantity": 3}]}` | **422** `OVERRIDE_REASON_REQUIRED` |
| ☐ | Same, adding `"override_reason": "Family asked for less today"` | **200** `confirmed` |
| ☐ | `GET /api/v1/listings/{listing_id}` (L4) | `allocated_quantity` 28, `remaining_quantity` 2, still `active` |
| ☐ | Family: `GET /api/v1/requests` | its request is `partially_approved`, with `allocation.approved_quantity` **3** |
| ☐ | Family: `GET /api/v1/allocations` | one allocation of 3 packages of "Bread packages" |
| ☐ | Provider: `POST /api/v1/allocations/{allocation_id}/cancel` (the family's allocation) with `{"reason": "Family can't come today"}` | **200** `cancelled`; the listing's `remaining_quantity` goes back to 5 |
| ☐ | Admin: in PowerShell, `docker exec freshlink-db psql -U freshlink -d freshlink -c "SELECT action, reason FROM audit_logs ORDER BY created_at DESC LIMIT 2;"` | `allocation.cancel` and `allocation.confirm` (with the override reason) |


### 10.10 Matching and "Recommended for you" (Stage 5b)

Use the three receivers from 10.9. Give each a capacity for **Bakery items** (`PUT /api/v1/receivers/me/food-preferences`):
NGO 20 packages, kitchen 10 packages, family 10 packages. As the provider, create a new Bakery items listing,
"Fresh naan" (`package`, 30), pickup window ending in 30 h (📋 **L5**).

| ✓ | Step | Expected |
|---|---|---|
| ☐ | Provider: `GET /api/v1/listings/{listing_id}/matches` (L5) | **200**. `model_version` `heuristic-v1`; each match has `rank`, `score` (0–1), the six `components` and `reasons` such as *"5.9 km away · accepts Bakery items · can take up to 20 packages"*. **No phone or address** in `receiver` |
| ☐ | Look at `excluded` | receivers that don't qualify, each with a reason (for example the volunteer's account isn't a receiver, so it isn't listed at all; any receiver without Bakery items in its preferences shows *"Doesn't accept Bakery items…"*) |
| ☐ | As the family: change its preferences to **Dairy products** only, then the provider repeats the call | the family moves to `excluded` with *"Doesn't accept Bakery items…"* |
| ☐ | Put the family's Bakery items preference back | it's matched again |
| ☐ | NGO: `GET /api/v1/listings/recommended` | **200**: "Fresh naan" with `match.score` and `match.reasons` |
| ☐ | NGO: request L5 (quantity 10), then `GET /api/v1/listings/recommended` again | L5 is no longer recommended (already requested) |
| ☐ | Kitchen: request L5 (quantity 5). Provider: `GET /api/v1/listings/{listing_id}/requests` (L5) with `sort` = `match` | both requests with a `match` score; the better match first |
| ☐ | Provider: `POST /api/v1/listings/{listing_id}/matches/refresh` | **200**, `stored` = number of matches |
| ☐ | In PowerShell: `docker exec freshlink-db psql -U freshlink -d freshlink -c "SELECT rank, score, outcome FROM match_recommendations ORDER BY generated_at DESC, rank LIMIT 5;"` | stored rows; the NGO's row has `outcome` **requested** |

💡 Viva note: this is **not machine learning**. It's an explainable scoring formula (plan 7.4, v1). The stored outcomes are the data a learned version (v2) would be trained and evaluated on.


### 10.11 Collection: handover and receipt (Stage 6a)

Continue with the listing L4 from 10.9, where the NGO and the kitchen have approved allocations.

| ✓ | Step | Expected |
|---|---|---|
| ☐ | Provider: `GET /api/v1/collections` | one `scheduled` collection per approved allocation. 📋 copy the NGO's collection `id` as **C1** |
| ☐ | NGO: `GET /api/v1/collections/{collection_id}` (C1) | the **provider's** phone, contact person and pickup address are shown: allowed inside this transaction |
| ☐ | NGO: `PATCH /api/v1/collections/{collection_id}` (C1) with `{"scheduled_pickup_at": "<a time inside the pickup window, +05:00>"}` | **200**, `version` 2 |
| ☐ | NGO: `POST .../handover` with `{"quantity": 15}` | **403** (only the provider confirms the handover) |
| ☐ | Provider: `POST .../handover` with `{"quantity": 16}` | **422** `QUANTITY_TOO_HIGH` (more than approved) |
| ☐ | Provider: `POST .../handover` with `{"quantity": 15, "notes": "3 crates"}` | **200** `handed_over` |
| ☐ | NGO: `POST .../receipt` with `{"quantity": 16}` | **422** `QUANTITY_TOO_HIGH` (more than handed over) |
| ☐ | NGO: `POST .../receipt` with `{"quantity": 14, "notes": "1 package damaged"}` | **200** `received`: approved 15, handed over 15, received **14** (kept separately) |
| ☐ | NGO: `GET /api/v1/requests/{request_id}` (its request) | `completed`; the history ends with *received 14 packages* |
| ☐ | Kitchen: `POST /api/v1/collections/{collection_id}/fail` (its collection) with `{"reason": "Our van broke down"}` | **200** `failed`; the listing's `remaining_quantity` grows again (the food never left the provider) |


### 10.12 Volunteer delivery (Stage 6b)

**Set up.** The volunteer from section 7 lives in Johar Town, about **10.1 km** from the bakery, but its service area is only 8 km.
So first, as the volunteer, widen it: `PUT /api/v1/volunteers/me` with the section 7 body but `"service_radius_km": 15`.
(Try `GET /api/v1/assignments/open` before and after: with 8 km you'll see no task, which is the service-area rule working.) As the provider, create a listing
"Rice" (Cooked meals, `kg`, 10, preparation 1 h ago, pickup window 1 h to 5 h from now). As the NGO, request **5 kg with
`"collection_preference": "needs_volunteer"`**. As the provider, approve it (`POST /api/v1/requests/{request_id}/approve`).
A delivery task opens automatically. Log in as the volunteer (password `New-Fresh-2026` since section 9).

| ✓ | Step | Expected |
|---|---|---|
| ☐ | Volunteer: `GET /api/v1/assignments/open` | the task, with `distance_km`; `dropoff_address`, `provider_contact` and `receiver_contact` are **null** (private until accepted). 📋 copy its `id` as **T1** |
| ☐ | Admin: `GET /api/v1/assignments/{task_id}/suggested-volunteers` (T1) | the volunteer, with a score and reasons such as *"…km from the pickup · has a motorbike · no other tasks"* |
| ☐ | Volunteer: `POST /api/v1/assignments/{task_id}/claim` (T1) | **200** `accepted`; now `dropoff_address` and both phone numbers are shown |
| ☐ | Volunteer: `POST .../status` with `{"status": "delivered"}` | **409** `INVALID_STATUS_TRANSITION` (must collect first) |
| ☐ | Volunteer: `{"status": "en_route_pickup"}` | **200** |
| ☐ | Volunteer: `{"status": "collected"}` | **422** `QUANTITY_REQUIRED` |
| ☐ | Volunteer: `{"status": "collected", "quantity": 5, "note": "in a pot"}` | **200** `collected`; the collection is now `handed_over` |
| ☐ | Volunteer: `{"status": "en_route_dropoff"}`, then `{"status": "delivered"}` | **200** each |
| ☐ | NGO: `POST /api/v1/collections/{collection_id}/receipt` with `{"quantity": 5}` | **200**; the task becomes `completed` |
| ☐ | Volunteer: `GET /api/v1/assignments/{task_id}/history` | open → accepted → en_route_pickup → collected → en_route_dropoff → delivered → completed |

**Optional: decline and reassign.** Create another needs-volunteer request and approve it. As admin, `POST /api/v1/assignments/{task_id}/assign`
with the volunteer's profile `id` (from suggested-volunteers). As the volunteer, `POST .../decline` with
`{"reason": "Exam tomorrow, sorry"}`: the task is `declined` and a new `open` task appears.

### 10.13 Notifications (Stage 7)

Everything you did above has already left notifications behind. No new set-up is needed.

| ✓ | Step | Expected |
|---|---|---|
| ☐ | Provider: `GET /api/v1/notifications/unread-count` | **200** `{"unread": N}` with N > 0 |
| ☐ | Provider: `GET /api/v1/notifications` | newest first: e.g. *"Food received"*, *"New food request"*; each has a `link` such as `/requests/…`. 📋 copy one `id` as **N1** |
| ☐ | NGO: `GET /api/v1/notifications` | e.g. *"Request approved"* (*"You will receive 5 kg of Rice…"*), *"Food handed over"*, *"Food you may need"* (a new listing that matches you) |
| ☐ | Provider (same list) | *"More information needed"* and *"Account approved"* from section 10.4 |
| ☐ | Volunteer: `GET /api/v1/notifications` | if you did the optional part of 10.12: *"New delivery task"* (assigned by the admin) |
| ☐ | Admin: `GET /api/v1/notifications` | *"New registration"* for each account and *"Documents to review"* |
| ☐ | NGO: `POST /api/v1/notifications/{notification_id}/read` with **N1** (the provider's) | **404** `NOT_FOUND` (you can't touch someone else's) |
| ☐ | Provider: `POST /api/v1/notifications/{notification_id}/read` (N1) | **200** `"is_read": true`; the unread count goes down by 1 |
| ☐ | Provider: `POST /api/v1/notifications/read-all` | **200** `{"marked": …}`; then `?unread_only=true` returns `total: 0` |
| ☐ | Admin: `POST /api/v1/admin/announcements` with `{"title": "Eid schedule", "body": "Pickups close at 6 pm on Eid.", "audience_type": "role", "audience_role": "receiver"}` | **201** with `recipient_count` (receivers only); the NGO sees it, the volunteer doesn't |
| ☐ | Admin: same, `"audience_type": "role"` but no `audience_role` | **422** |
| ☐ | Provider: `POST /api/v1/admin/announcements` | **403** `FORBIDDEN` |
| ☐ | Admin: `PUT /api/v1/admin/notification-categories/request_new` with `{"is_enabled": false}`; NGO makes a new request | the provider gets **no** "New food request". Switch it back with `{"is_enabled": true}` |


---

## 11. Clean up the test accounts

This deletes only the `guide.` accounts. Volunteer tasks, collections, allocations, suggestions, requests and then listings must be deleted first (the database protects history: a receiver with requests, or a provider with listings, can't simply be deleted); profiles, sessions and listing history then go automatically (ON DELETE CASCADE).

```powershell
docker exec freshlink-db psql -U freshlink -d freshlink -c "DELETE FROM volunteer_assignments WHERE collection_id IN (SELECT c.id FROM collections c JOIN allocations a ON a.id = c.allocation_id JOIN receiver_profiles r ON r.id = a.receiver_id JOIN users u ON u.id = r.user_id WHERE u.email LIKE 'guide.%');"
docker exec freshlink-db psql -U freshlink -d freshlink -c "DELETE FROM collections WHERE allocation_id IN (SELECT a.id FROM allocations a JOIN receiver_profiles r ON r.id = a.receiver_id JOIN users u ON u.id = r.user_id WHERE u.email LIKE 'guide.%');"
docker exec freshlink-db psql -U freshlink -d freshlink -c "DELETE FROM allocations WHERE receiver_id IN (SELECT r.id FROM receiver_profiles r JOIN users u ON u.id = r.user_id WHERE u.email LIKE 'guide.%');"
docker exec freshlink-db psql -U freshlink -d freshlink -c "DELETE FROM allocation_runs WHERE listing_id IN (SELECT l.id FROM food_listings l JOIN provider_profiles p ON p.id = l.provider_id JOIN users u ON u.id = p.user_id WHERE u.email LIKE 'guide.%');"
docker exec freshlink-db psql -U freshlink -d freshlink -c "DELETE FROM food_requests WHERE receiver_id IN (SELECT r.id FROM receiver_profiles r JOIN users u ON u.id = r.user_id WHERE u.email LIKE 'guide.%');"
docker exec freshlink-db psql -U freshlink -d freshlink -c "DELETE FROM food_listings WHERE provider_id IN (SELECT p.id FROM provider_profiles p JOIN users u ON u.id = p.user_id WHERE u.email LIKE 'guide.%');"
docker exec freshlink-db psql -U freshlink -d freshlink -c "DELETE FROM users WHERE email LIKE 'guide.%';"
```
☐ The last command shows `DELETE 6` (provider, receiver, volunteer, admin, kitchen, family)

Notes:
- The **audit log keeps its entries**. That's on purpose: history is never deleted.
- Uploaded files stay in `backend\storage` (git-ignored). You can delete that folder's contents by hand if you want.

---

## 12. Automated tests (the same checks and more, in about 40 seconds)

```powershell
cd D:\University\FreshLink\backend
.\.venv\Scripts\Activate.ps1
pytest
```
☐ `409 passed`. Docker must be running. The tests use a separate `freshlink_test` database, so your data is never touched.

---

### If something goes wrong
| You see | Cause | Fix |
|---|---|---|
| `alembic`/`uvicorn` *is not recognized* | venv not activated | `.\.venv\Scripts\Activate.ps1` |
| *running scripts is disabled* | Windows policy | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `Cannot connect to PostgreSQL` / health 503 | Docker Desktop closed | Start Docker Desktop, then `docker compose up -d` |
| `TOKEN_EXPIRED` | 15 minutes passed | Log in again and re-authorize |
| `TOO_MANY_LOGIN_ATTEMPTS` | 5 wrong passwords for one account (or 20 from your computer) | Wait 60 seconds |
