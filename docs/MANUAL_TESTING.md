# FreshLink — Manual Testing Guide (Stages 0–2a)

This guide tests **every API built so far (25 endpoints)** by hand in Swagger UI.
Each step says exactly what to type and what you should see.

- **Time needed:** about 45 minutes
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

---

## 11. Clean up the test accounts

This deletes only the `guide.` accounts. Their profiles and sessions are deleted with them automatically (ON DELETE CASCADE).

```powershell
docker exec freshlink-db psql -U freshlink -d freshlink -c "DELETE FROM users WHERE email LIKE 'guide.%';"
```
☐ Output `DELETE 4`

---

## 12. Automated tests (the same checks and more, in about 40 seconds)

```powershell
cd D:\University\FreshLink\backend
.\.venv\Scripts\Activate.ps1
pytest
```
☐ `229 passed`. Docker must be running. The tests use a separate `freshlink_test` database, so your data is never touched.

---

### If something goes wrong
| You see | Cause | Fix |
|---|---|---|
| `alembic`/`uvicorn` *is not recognized* | venv not activated | `.\.venv\Scripts\Activate.ps1` |
| *running scripts is disabled* | Windows policy | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `Cannot connect to PostgreSQL` / health 503 | Docker Desktop closed | Start Docker Desktop, then `docker compose up -d` |
| `TOKEN_EXPIRED` | 15 minutes passed | Log in again and re-authorize |
| `TOO_MANY_LOGIN_ATTEMPTS` | 5 wrong passwords for one account (or 20 from your computer) | Wait 60 seconds |
