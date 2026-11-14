"""Stage 8a: admin user management - search, view, activate / deactivate / suspend, history,
creating administrators, and what suspension means for the rest of the platform."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import AccountStatus, UserRole, VerificationStatus
from app.models.audit import AuditLog
from app.models.verification import VerificationRequest
from tests.api.test_notifications import _request, food, scene  # noqa: F401  (fixtures)
from tests.conftest import DEFAULT_PASSWORD, MakeUser, login_headers

APPROVED = VerificationStatus.APPROVED
URL = "/api/v1/admin/users"


@pytest.fixture
def admin(client: TestClient, make_user: MakeUser) -> dict:
    return login_headers(client, make_user(role=UserRole.ADMIN, verification_status=APPROVED,
                                           full_name="Main Admin").email)


@pytest.fixture
def super_admin(client: TestClient, make_user: MakeUser) -> dict:
    return login_headers(client, make_user(role=UserRole.ADMIN, verification_status=APPROVED,
                                           is_super_admin=True, full_name="Super Admin").email)


def _login(client: TestClient, email: str, password: str = DEFAULT_PASSWORD) -> object:
    return client.post("/api/v1/auth/login", json={"identifier": email, "password": password, "platform": "mobile"})


# =================================================================== list / view


def test_search_and_filter(client: TestClient, admin: dict, make_user: MakeUser, scene: dict) -> None:
    make_user(role=UserRole.VOLUNTEER, full_name="Bilal Ahmed", phone="+923331234567")
    make_user(role=UserRole.VOLUNTEER, full_name="Zara Khan", account_status=AccountStatus.SUSPENDED)

    def names(query: str) -> list[str]:
        response = client.get(f"{URL}?{query}", headers=admin)
        assert response.status_code == 200, response.text
        return [u["full_name"] for u in response.json()["items"]]

    assert names("q=bilal") == ["Bilal Ahmed"]
    assert names("q=0333 1234") == ["Bilal Ahmed"]  # local phone format still matches
    assert names("q=hope ngo") == ["Test User"]  # the receiver's organisation name
    assert names("role=volunteer") == ["Zara Khan", "Bilal Ahmed"]  # newest first
    assert names("account_status=suspended") == ["Zara Khan"]
    assert names("q=100%") == []  # % is a literal, not a wildcard
    page = client.get(f"{URL}?page_size=2&page=2", headers=admin).json()
    assert page["total"] == 5 and len(page["items"]) == 2
    receiver = client.get(f"{URL}?role=receiver", headers=admin).json()["items"][0]
    assert receiver["profile_name"] == "Hope NGO" and "password_hash" not in receiver
    assert client.get(URL, headers=scene["provider"]).status_code == 403


def test_user_detail(client: TestClient, admin: dict, scene: dict) -> None:
    _request(client, scene)
    user_id = scene["receiver_user"].id
    detail = client.get(f"{URL}/{user_id}", headers=admin).json()
    assert detail["profile"]["receiver_name"] == "Hope NGO" and detail["profile"]["is_eligible"] is True
    assert detail["counts"] == {"requests": 1, "completed_collections": 0}
    provider = client.get(f"{URL}/{scene['provider_user'].id}", headers=admin).json()
    assert provider["counts"]["listings"] == 1 and provider["profile"]["category"] == "Bakery"
    assert client.get(f"{URL}/{uuid.uuid4()}", headers=admin).status_code == 404


# ================================================================ status changes


def test_suspend_ends_sessions_and_blocks_login(client: TestClient, admin: dict, make_user: MakeUser,
                                                db_session: Session) -> None:
    user = make_user(role=UserRole.VOLUNTEER, verification_status=APPROVED)
    session = _login(client, user.email).json()
    old = {"Authorization": f"Bearer {session['access_token']}"}

    assert client.post(f"{URL}/{user.id}/suspend", headers=admin, json={}).status_code == 422  # reason required
    short = client.post(f"{URL}/{user.id}/suspend", headers=admin, json={"reason": "  no "})
    assert short.status_code == 422 and short.json()["error"]["details"]["fields"][0]["field"] == "body.reason"

    done = client.post(f"{URL}/{user.id}/suspend", headers=admin, json={"reason": "Repeated no-shows"})
    assert done.status_code == 200 and done.json()["account_status"] == "suspended"
    assert client.get("/api/v1/auth/me", headers=old).json()["error"]["code"] == "TOKEN_REVOKED"  # at once
    refresh = client.post("/api/v1/auth/refresh", json={"refresh_token": session["refresh_token"],
                                                         "platform": "mobile"})
    assert refresh.status_code == 401  # the refresh token is revoked too
    assert _login(client, user.email).json()["error"]["code"] == "ACCOUNT_SUSPENDED"

    again = client.post(f"{URL}/{user.id}/suspend", headers=admin, json={"reason": "Repeated no-shows"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "STATUS_UNCHANGED"
    entry = db_session.scalar(select(AuditLog).where(AuditLog.action == "user.suspend"))
    assert entry.reason == "Repeated no-shows" and entry.after == {"account_status": "suspended"}

    back = client.post(f"{URL}/{user.id}/activate", headers=admin, json={"reason": "Talked to them"})
    assert back.status_code == 200 and back.json()["account_status"] == "active"
    fresh = {"Authorization": f"Bearer {_login(client, user.email).json()['access_token']}"}
    note = client.get("/api/v1/notifications", headers=fresh).json()["items"][0]
    assert note["title"] == "Account reactivated" and note["body"].endswith("Note: Talked to them")


def test_deactivate(client: TestClient, admin: dict, make_user: MakeUser) -> None:
    user = make_user(role=UserRole.RECEIVER)
    assert client.post(f"{URL}/{user.id}/deactivate", headers=admin,
                       json={"reason": "Organisation closed"}).json()["account_status"] == "deactivated"
    assert _login(client, user.email).json()["error"]["code"] == "ACCOUNT_DEACTIVATED"


def test_admin_accounts_are_protected(client: TestClient, admin: dict, super_admin: dict,
                                      make_user: MakeUser, db_session: Session) -> None:
    me = client.get("/api/v1/auth/me", headers=admin).json()
    own = client.post(f"{URL}/{me['id']}/suspend", headers=admin, json={"reason": "Testing myself"})
    assert own.status_code == 409 and own.json()["error"]["code"] == "CANNOT_CHANGE_OWN_ACCOUNT"

    other_admin = make_user(role=UserRole.ADMIN, verification_status=APPROVED)
    assert client.post(f"{URL}/{other_admin.id}/suspend", headers=admin,
                       json={"reason": "Not allowed for me"}).status_code == 403  # normal admin
    assert client.post(f"{URL}/{other_admin.id}/suspend", headers=super_admin,
                       json={"reason": "Left the team"}).status_code == 200  # super admin may
    assert client.post(f"{URL}/{uuid.uuid4()}/suspend", headers=admin,
                       json={"reason": "Nobody here"}).status_code == 404


def test_suspended_provider_food_disappears(client: TestClient, admin: dict, scene: dict) -> None:
    receiver, listing = scene["receiver"], scene["listing"]
    assert client.get("/api/v1/listings", headers=receiver).json()["total"] == 1
    client.post(f"{URL}/{scene['provider_user'].id}/suspend", headers=admin, json={"reason": "Licence expired"})
    assert client.get("/api/v1/listings", headers=receiver).json()["total"] == 0
    blocked = client.post("/api/v1/requests", headers=receiver,
                          json={"listing_id": listing["id"], "requested_quantity": 5})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "LISTING_NOT_AVAILABLE"
    assert client.get("/api/v1/me/dashboard", headers=receiver).json()["available_food"] == 0

    client.post(f"{URL}/{scene['provider_user'].id}/activate", headers=admin, json={})
    assert client.get("/api/v1/listings", headers=receiver).json()["total"] == 1  # back again


# ======================================================================= history


def test_account_history(client: TestClient, admin: dict, make_user: MakeUser, db_session: Session) -> None:
    user = make_user(role=UserRole.PROVIDER, verification_status=VerificationStatus.PENDING)
    db_session.add(VerificationRequest(user_id=user.id, note="Licence attached"))
    db_session.commit()
    request_id = db_session.scalar(select(VerificationRequest.id))
    client.post(f"/api/v1/admin/verifications/{request_id}/reject", headers=admin,
                json={"reason": "The licence is expired."})
    client.post(f"{URL}/{user.id}/suspend", headers=admin, json={"reason": "Fake documents"})

    items = client.get(f"{URL}/{user.id}/history", headers=admin).json()
    kinds = [i["kind"] for i in items]
    assert kinds[0] == "user.suspend" and items[0]["reason"] == "Fake documents"
    assert "verification.rejected" in kinds and "verification.submitted" in kinds
    assert "verification.reject" in kinds  # the admin's audited decision
    assert kinds[-1] == "account.registered"
    assert next(i for i in items if i["kind"] == "verification.submitted")["reason"] == "Licence attached"


# ============================================================== create an admin


def test_create_admin(client: TestClient, admin: dict, super_admin: dict, db_session: Session) -> None:
    body = {"email": "New.Admin@Example.com", "phone": "0300 5556677", "full_name": "  New   Admin ",
            "password": "Strong-Admin-2026"}
    assert client.post("/api/v1/admin/admins", headers=admin, json=body).status_code == 403  # not super
    weak = client.post("/api/v1/admin/admins", headers=super_admin, json={**body, "password": "password123"})
    assert weak.status_code == 422

    created = client.post("/api/v1/admin/admins", headers=super_admin, json=body)
    assert created.status_code == 201, created.text
    out = created.json()
    assert (out["email"], out["full_name"], out["role"]) == ("new.admin@example.com", "New Admin", "admin")
    assert out["is_super_admin"] is False and out["is_verified"] is True
    assert _login(client, "new.admin@example.com", "Strong-Admin-2026").status_code == 200

    duplicate = client.post("/api/v1/admin/admins", headers=super_admin, json=body)
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"
    entry = db_session.scalar(select(AuditLog).where(AuditLog.action == "admin.create"))
    assert entry.after == {"email": "new.admin@example.com", "is_super_admin": False}
    assert "Strong-Admin-2026" not in str(entry.after)  # never the password
