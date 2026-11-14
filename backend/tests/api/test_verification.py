"""Stage 2b: private files, verification submission, admin review, eligibility, audit log."""

import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.enums import UserRole, VerificationStatus
from app.main import create_app
from app.models.audit import AuditLog
from app.models.category import ProviderCategory, ReceiverCategory
from app.models.user import User
from app.models.verification import VerificationRequest
from app.services import verification_service
from app.services.audit_service import ClientInfo
from tests.conftest import MakeUser, login_headers

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
EXE = b"MZ\x90\x00" + b"\x00" * 64  # a Windows program pretending to be a PDF
LAHORE = {"address": "Main Boulevard, Lahore", "latitude": 31.52, "longitude": 74.35}


def _code(response) -> str:  # noqa: ANN001
    return response.json()["error"]["code"]


def _upload(client: TestClient, headers: dict, data: bytes = PDF, name: str = "doc.pdf",
            purpose: str = "verification_doc", content_type: str = "application/pdf"):  # noqa: ANN202
    return client.post("/api/v1/files", headers=headers, files={"file": (name, data, content_type)},
                       data={"purpose": purpose})


@pytest.fixture
def provider(client: TestClient, make_user: MakeUser) -> tuple[User, dict]:
    user = make_user(role=UserRole.PROVIDER)
    return user, login_headers(client, user.email)


@pytest.fixture
def admin(client: TestClient, make_user: MakeUser) -> tuple[User, dict]:
    user = make_user(role=UserRole.ADMIN, verification_status=VerificationStatus.APPROVED)
    return user, login_headers(client, user.email)


def _provider_profile(client: TestClient, headers: dict, db: Session) -> None:
    category = db.scalar(select(ProviderCategory.id).where(ProviderCategory.code == "bakery"))
    body = {**LAHORE, "business_name": "Test Bakers", "provider_category_id": str(category),
            "contact_person": "Ali Raza", "contact_phone": "0300 1234567"}
    assert client.put("/api/v1/providers/me", headers=headers, json=body).status_code == 200


def _submit_provider(client: TestClient, headers: dict, db: Session) -> dict:
    """Profile + upload + submit: returns the created request."""
    _provider_profile(client, headers, db)
    file_id = _upload(client, headers).json()["id"]
    response = client.post("/api/v1/me/verification", headers=headers,
                           json={"documents": [{"file_id": file_id, "document_type": "business_proof"}],
                                 "note": "Licence attached"})
    assert response.status_code == 201, response.text
    return response.json()


# ====================================================================== files


@pytest.mark.parametrize(("data", "mime"), [(PDF, "application/pdf"), (PNG, "image/png"), (JPG, "image/jpeg")])
def test_upload_accepted_types(client: TestClient, provider: tuple, data: bytes, mime: str) -> None:
    _, headers = provider
    response = _upload(client, headers, data, name="C:\\fakepath\\my licence.pdf", content_type="anything/at-all")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["mime_type"] == mime  # decided from the file's content, not what the browser said
    assert body["size_bytes"] == len(data)
    assert body["original_name"] == "my licence.pdf"  # folder part removed


def test_file_is_stored_privately_under_a_random_name(
    client: TestClient, provider: tuple, test_settings: Settings
) -> None:
    _, headers = provider
    _upload(client, headers, name="cnic.pdf")
    stored = [p for p in test_settings.storage_dir.rglob("*") if p.is_file()]
    assert len(stored) == 1
    assert stored[0].name != "cnic.pdf" and len(stored[0].name) == 32
    assert stored[0].read_bytes() == PDF


def test_disguised_file_rejected(client: TestClient, provider: tuple) -> None:
    response = _upload(client, provider[1], EXE, name="cnic.pdf", content_type="application/pdf")
    assert response.status_code == 415 and _code(response) == "UNSUPPORTED_FILE_TYPE"


def test_empty_file_rejected(client: TestClient, provider: tuple) -> None:
    response = _upload(client, provider[1], b"")
    assert response.status_code == 422 and _code(response) == "EMPTY_FILE"


def test_too_large_file_rejected(test_settings: Settings, make_user: MakeUser) -> None:
    with TestClient(create_app(test_settings.model_copy(update={"max_upload_mb": 1}))) as client:
        headers = login_headers(client, make_user().email)
        response = _upload(client, headers, PDF + b"x" * (1024 * 1024))
    assert response.status_code == 413 and _code(response) == "FILE_TOO_LARGE"


def test_upload_limit_per_user(test_settings: Settings, make_user: MakeUser) -> None:
    with TestClient(create_app(test_settings.model_copy(update={"max_files_per_user": 2}))) as client:
        headers = login_headers(client, make_user().email)
        assert [_upload(client, headers).status_code for _ in range(3)] == [201, 201, 409]


@pytest.mark.parametrize(("purpose", "status", "code"),
                         [("listing_image", 422, "PURPOSE_NOT_AVAILABLE"), ("selfie", 422, "VALIDATION_ERROR")])
def test_upload_purpose_rules(client: TestClient, provider: tuple, purpose: str, status: int, code: str) -> None:
    response = _upload(client, provider[1], purpose=purpose)
    assert response.status_code == status and _code(response) == code


def test_upload_needs_login_and_is_not_for_admins(client: TestClient, admin: tuple) -> None:
    assert _upload(client, {}).status_code == 401
    response = _upload(client, admin[1])
    assert response.status_code == 403


def test_download_only_by_owner_and_admins(
    client: TestClient, provider: tuple, admin: tuple, make_user: MakeUser
) -> None:
    _, owner_headers = provider
    file_id = _upload(client, owner_headers, PNG, name="shop.png").json()["id"]
    url = f"/api/v1/files/{file_id}"

    own = client.get(url, headers=owner_headers)
    assert own.status_code == 200 and own.content == PNG
    assert own.headers["content-type"] == "image/png"
    assert own.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in own.headers["cache-control"]
    assert client.get(url, headers=admin[1]).status_code == 200

    stranger = login_headers(client, make_user(role=UserRole.RECEIVER).email)
    response = client.get(url, headers=stranger)
    assert response.status_code == 404  # 404, not 403: strangers can't even learn it exists
    assert client.get(url).status_code == 401
    assert client.get("/api/v1/files/00000000-0000-0000-0000-000000000000", headers=owner_headers).status_code == 404


# ================================================================ requirements


def _ids(db: Session, model) -> dict[str, str]:  # noqa: ANN001
    return {c.code: str(c.id) for c in db.scalars(select(model))}


def test_requirements_per_role_and_category(client: TestClient, db_session: Session) -> None:
    def types(params: str) -> dict[str, bool]:
        response = client.get(f"/api/v1/lookups/verification-requirements?{params}")
        assert response.status_code == 200
        return {r["document_type"]: r["is_required"] for r in response.json()}

    receivers = _ids(db_session, ReceiverCategory)
    assert types("role=provider") == {"business_proof": True, "storefront_photo": False}
    assert types("role=volunteer") == {"identity_document": True}
    assert types(f"role=receiver&receiver_category_id={receivers['ngo']}") == {"registration_certificate": True}
    assert types(f"role=receiver&receiver_category_id={receivers['individual']}") == {"identity_document": True}
    assert types("role=admin") == {}


def test_category_without_documents_needs_none(client: TestClient, db_session: Session) -> None:
    receivers = _ids(db_session, ReceiverCategory)
    db_session.execute(text("UPDATE receiver_categories SET requires_documents = false WHERE code = 'ngo'"))
    db_session.commit()
    try:
        response = client.get(f"/api/v1/lookups/verification-requirements?role=receiver&receiver_category_id={receivers['ngo']}")
        assert response.json() == []
    finally:
        db_session.execute(text("UPDATE receiver_categories SET requires_documents = true WHERE code = 'ngo'"))
        db_session.commit()


# ============================================================ user submission


def test_status_before_profile(client: TestClient, provider: tuple) -> None:
    data = client.get("/api/v1/me/verification", headers=provider[1]).json()
    assert data["verification_status"] == "not_submitted"
    assert data["profile_completed"] is False and data["can_submit"] is False
    assert {r["document_type"] for r in data["requirements"]} == {"business_proof", "storefront_photo"}
    assert data["latest_request"] is None


def test_submit_needs_profile(client: TestClient, provider: tuple) -> None:
    file_id = _upload(client, provider[1]).json()["id"]
    response = client.post("/api/v1/me/verification", headers=provider[1],
                           json={"documents": [{"file_id": file_id, "document_type": "business_proof"}]})
    assert response.status_code == 409 and _code(response) == "PROFILE_REQUIRED"


def test_successful_submission(client: TestClient, provider: tuple, db_session: Session) -> None:
    user, headers = provider
    request = _submit_provider(client, headers, db_session)
    assert request["status"] == "pending" and request["note"] == "Licence attached"
    assert request["documents"][0]["document_type"] == "business_proof"

    data = client.get("/api/v1/me/verification", headers=headers).json()
    assert data["verification_status"] == "pending" and data["can_submit"] is False
    assert data["latest_request"]["id"] == request["id"]
    assert client.get("/api/v1/auth/me", headers=headers).json()["verification_status"] == "pending"

    again = client.post("/api/v1/me/verification", headers=headers, json={"documents": []})
    assert again.status_code == 409 and _code(again) == "VERIFICATION_ALREADY_PENDING"


def test_submission_validation(client: TestClient, provider: tuple, make_user: MakeUser, db_session: Session) -> None:
    _, headers = provider
    _provider_profile(client, headers, db_session)
    mine = _upload(client, headers).json()["id"]
    photo = _upload(client, headers, PNG).json()["id"]
    other_headers = login_headers(client, make_user().email)
    someone_elses = _upload(client, other_headers).json()["id"]

    def submit(docs: list) -> object:
        return client.post("/api/v1/me/verification", headers=headers, json={"documents": docs})

    missing = submit([{"file_id": photo, "document_type": "storefront_photo"}])  # optional only
    assert missing.status_code == 422 and _code(missing) == "MISSING_REQUIRED_DOCUMENTS"
    assert missing.json()["error"]["details"]["missing"] == ["business_proof"]
    assert "Proof of business" in missing.json()["error"]["message"]

    unknown = submit([{"file_id": mine, "document_type": "passport"}])
    assert unknown.status_code == 422 and _code(unknown) == "UNKNOWN_DOCUMENT_TYPE"

    stolen = submit([{"file_id": someone_elses, "document_type": "business_proof"}])
    assert stolen.status_code == 422 and _code(stolen) == "INVALID_FILE"

    twice = submit([{"file_id": mine, "document_type": "business_proof"}, {"file_id": mine, "document_type": "storefront_photo"}])
    assert twice.status_code == 422 and _code(twice) == "VALIDATION_ERROR"

    ok = submit([{"file_id": mine, "document_type": "business_proof"}, {"file_id": photo, "document_type": "storefront_photo"}])
    assert ok.status_code == 201 and len(ok.json()["documents"]) == 2


def test_database_allows_only_one_pending_request_per_user(make_user: MakeUser, db_session: Session) -> None:
    user = make_user()
    db_session.add(VerificationRequest(user_id=user.id))
    db_session.commit()
    db_session.add(VerificationRequest(user_id=user.id))
    with pytest.raises(IntegrityError, match="uq_verification_requests_one_pending_per_user"):
        db_session.commit()


# ================================================================ admin review


def test_admin_endpoints_are_admin_only(client: TestClient, provider: tuple) -> None:
    for method, path in [("GET", "/api/v1/admin/verifications"),
                         ("POST", "/api/v1/admin/verifications/00000000-0000-0000-0000-000000000000/approve"),
                         ("PATCH", "/api/v1/admin/receivers/00000000-0000-0000-0000-000000000000/eligibility")]:
        assert client.request(method, path, json={}).status_code == 401
        assert client.request(method, path, json={}, headers=provider[1]).status_code == 403


def test_queue_and_detail(client: TestClient, provider: tuple, admin: tuple, db_session: Session) -> None:
    user, headers = provider
    request = _submit_provider(client, headers, db_session)

    queue = client.get("/api/v1/admin/verifications", headers=admin[1]).json()
    assert queue["total"] == 1 and queue["page"] == 1
    item = queue["items"][0]
    assert item["id"] == request["id"] and item["role"] == "provider" and item["document_count"] == 1

    assert client.get("/api/v1/admin/verifications?role=receiver", headers=admin[1]).json()["total"] == 0
    assert client.get("/api/v1/admin/verifications?status=approved", headers=admin[1]).json()["total"] == 0

    detail = client.get(f"/api/v1/admin/verifications/{request['id']}", headers=admin[1]).json()
    assert detail["user"]["email"] == user.email
    assert detail["provider_profile"]["business_name"] == "Test Bakers"
    assert detail["receiver_profile"] is None
    assert {r["document_type"] for r in detail["requirements"]} == {"business_proof", "storefront_photo"}
    assert detail["history"] == []
    file_id = detail["documents"][0]["file"]["id"]
    assert client.get(f"/api/v1/files/{file_id}", headers=admin[1]).content == PDF  # admin can open it


def test_queue_pagination(client: TestClient, admin: tuple, make_user: MakeUser, db_session: Session) -> None:
    for _ in range(3):
        db_session.add(VerificationRequest(user_id=make_user().id))
    db_session.commit()
    page = client.get("/api/v1/admin/verifications?page=2&page_size=2", headers=admin[1]).json()
    assert page["total"] == 3 and len(page["items"]) == 1 and page["page"] == 2
    assert client.get("/api/v1/admin/verifications?page_size=101", headers=admin[1]).status_code == 422


def test_approve(client: TestClient, provider: tuple, admin: tuple, db_session: Session) -> None:
    user, headers = provider
    request = _submit_provider(client, headers, db_session)
    url = f"/api/v1/admin/verifications/{request['id']}"

    response = client.post(f"{url}/approve", headers={**admin[1], "User-Agent": "FreshLink-Test"},
                           json={"notes": "Licence checked"})
    assert response.status_code == 200 and response.json()["status"] == "approved"
    me = client.get("/api/v1/auth/me", headers=headers).json()
    assert me["verification_status"] == "approved" and me["is_verified"] is True

    log = db_session.scalar(select(AuditLog).where(AuditLog.action == "verification.approve"))
    assert log.actor_id == admin[0].id and log.actor_role == "admin" and log.entity_id == user.id
    assert log.before == {"verification_status": "pending"}
    assert log.after["verification_status"] == "approved"
    assert log.reason == "Licence checked" and log.user_agent == "FreshLink-Test" and log.ip

    twice = client.post(f"{url}/reject", headers=admin[1], json={"reason": "Changed my mind"})
    assert twice.status_code == 409 and _code(twice) == "ALREADY_REVIEWED"


def test_reject_then_resubmit(client: TestClient, provider: tuple, admin: tuple, db_session: Session) -> None:
    _, headers = provider
    request = _submit_provider(client, headers, db_session)
    url = f"/api/v1/admin/verifications/{request['id']}"

    too_short = client.post(f"{url}/reject", headers=admin[1], json={"reason": "no"})
    assert too_short.status_code == 422
    assert client.post(f"{url}/reject", headers=admin[1], json={"reason": "  The licence   is expired. "}).status_code == 200

    mine = client.get("/api/v1/me/verification", headers=headers).json()
    assert mine["verification_status"] == "rejected" and mine["can_submit"] is True
    assert mine["latest_request"]["review_notes"] == "The licence is expired."  # the user sees why

    file_id = _upload(client, headers).json()["id"]
    again = client.post("/api/v1/me/verification", headers=headers,
                        json={"documents": [{"file_id": file_id, "document_type": "business_proof"}]})
    assert again.status_code == 201
    detail = client.get(f"/api/v1/admin/verifications/{again.json()['id']}", headers=admin[1]).json()
    assert [h["status"] for h in detail["history"]] == ["rejected"]  # earlier attempt kept


def test_request_info(client: TestClient, provider: tuple, admin: tuple, db_session: Session) -> None:
    _, headers = provider
    request = _submit_provider(client, headers, db_session)
    response = client.post(f"/api/v1/admin/verifications/{request['id']}/request-info", headers=admin[1],
                           json={"reason": "Please upload a clearer photo of the licence."})
    assert response.status_code == 200 and response.json()["status"] == "info_requested"
    mine = client.get("/api/v1/me/verification", headers=headers).json()
    assert mine["verification_status"] == "info_requested" and mine["can_submit"] is True


def test_unknown_request(client: TestClient, admin: tuple) -> None:
    url = "/api/v1/admin/verifications/00000000-0000-0000-0000-000000000000"
    assert client.get(url, headers=admin[1]).status_code == 404
    assert client.post(f"{url}/approve", headers=admin[1], json={}).status_code == 404


def test_two_admins_deciding_at_once_only_one_wins(
    client: TestClient, provider: tuple, admin: tuple, make_user: MakeUser,
    db_session: Session, test_db_engine, monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    """Admin A approves while admin B rejects the same request at the same instant.
    The row lock makes B wait; B then sees it is no longer pending (409)."""
    request = _submit_provider(client, provider[1], db_session)
    second_admin = make_user(role=UserRole.ADMIN, verification_status=VerificationStatus.APPROVED)
    original_record = verification_service.record

    def slow_record(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        time.sleep(0.5)  # widen the gap between "check status" and "save" so both overlap
        return original_record(*args, **kwargs)

    monkeypatch.setattr(verification_service, "record", slow_record)
    barrier, results = threading.Barrier(2), []

    def decide(admin_user: User, decision: str) -> None:
        with Session(test_db_engine, expire_on_commit=False) as db:
            actor = db.get(User, admin_user.id)
            barrier.wait()
            try:
                verification_service.decide(db, actor, request["id"], decision, "reason text", ClientInfo(None, None))
                results.append("ok")
            except AppError as exc:
                results.append(exc.code)

    threads = [threading.Thread(target=decide, args=(admin[0], "approve")),
               threading.Thread(target=decide, args=(second_admin, "reject"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert sorted(results) == ["ALREADY_REVIEWED", "ok"]
    assert db_session.scalar(select(text("count(*)")).select_from(AuditLog)) == 1  # one decision, one log entry


# ================================================================= eligibility


def test_receiver_eligibility(client: TestClient, admin: tuple, make_user: MakeUser, db_session: Session) -> None:
    receiver = make_user(role=UserRole.RECEIVER)
    headers = login_headers(client, receiver.email)
    category = _ids(db_session, ReceiverCategory)["ngo"]
    client.put("/api/v1/receivers/me", headers=headers, json={**LAHORE, "receiver_name": "Hope Trust",
                                                              "receiver_category_id": category})
    url = f"/api/v1/admin/receivers/{receiver.id}/eligibility"

    no_reason = client.patch(url, headers=admin[1], json={"is_eligible": False})
    assert no_reason.status_code == 422 and _code(no_reason) == "REASON_REQUIRED"

    response = client.patch(url, headers=admin[1], json={"is_eligible": False, "notes": "Duplicate organization"})
    assert response.status_code == 200 and response.json()["is_eligible"] is False
    assert client.get("/api/v1/receivers/me", headers=headers).json()["is_eligible"] is False
    log = db_session.scalar(select(AuditLog).where(AuditLog.action == "receiver.eligibility"))
    assert log.before["is_eligible"] is True and log.after["is_eligible"] is False

    assert client.patch(url, headers=admin[1], json={"is_eligible": True}).json()["is_eligible"] is True
    provider = make_user(role=UserRole.PROVIDER)
    assert client.patch(f"/api/v1/admin/receivers/{provider.id}/eligibility", headers=admin[1],
                        json={"is_eligible": True}).status_code == 404


# =================================================================== audit log


def test_audit_log_cannot_be_changed_or_deleted(db_session: Session) -> None:
    db_session.add(AuditLog(action="test.entry", entity_type="test"))
    db_session.commit()
    for statement in ("UPDATE audit_logs SET action = 'tampered'", "DELETE FROM audit_logs"):
        with pytest.raises(DBAPIError, match="append-only"):
            db_session.execute(text(statement))
        db_session.rollback()
    assert db_session.scalar(select(AuditLog.action)) == "test.entry"


# ============================================== remaining edge cases (coverage)


@pytest.mark.parametrize("key", ["../../app/main.py", r"..\..\.env", "abc", "A" * 32, ""])
def test_storage_refuses_keys_that_could_escape_the_folder(test_settings: Settings, key: str) -> None:
    from app.core.storage import LocalStorage

    with pytest.raises(ValueError, match="Invalid storage key"):
        LocalStorage(test_settings.storage_dir).path_for(key)


def test_failed_database_save_removes_the_file_from_disk(
    client: TestClient, provider: tuple, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.orm import Session as OrmSession

    def broken_commit(self) -> None:  # noqa: ANN001
        raise RuntimeError("database write failed")

    monkeypatch.setattr(OrmSession, "commit", broken_commit)
    response = _upload(client, provider[1])
    assert response.status_code == 500
    assert [p for p in test_settings.storage_dir.rglob("*") if p.is_file()] == []  # no orphan file left


def test_verified_users_and_admins_cannot_submit(client: TestClient, admin: tuple, make_user: MakeUser) -> None:
    verified = login_headers(client, make_user(verification_status=VerificationStatus.APPROVED).email)
    response = client.post("/api/v1/me/verification", headers=verified, json={"documents": []})
    assert response.status_code == 409 and _code(response) == "ALREADY_VERIFIED"
    response = client.post("/api/v1/me/verification", headers=admin[1], json={"documents": []})
    assert response.status_code == 403
    assert client.get("/api/v1/me/verification", headers=admin[1]).json()["can_submit"] is False


def test_simultaneous_submissions_give_conflict_not_crash(
    client: TestClient, provider: tuple, db_session: Session
) -> None:
    """Simulates two devices submitting at the same instant: the user row still says
    'not submitted' when the second insert happens, so only the database index can stop it."""
    user, headers = provider
    _submit_provider(client, headers, db_session)
    db_session.execute(text("UPDATE users SET verification_status = 'not_submitted' WHERE id = :id"), {"id": user.id})
    db_session.commit()
    file_id = _upload(client, headers).json()["id"]
    response = client.post("/api/v1/me/verification", headers=headers,
                           json={"documents": [{"file_id": file_id, "document_type": "business_proof"}]})
    assert response.status_code == 409 and _code(response) == "VERIFICATION_ALREADY_PENDING"
