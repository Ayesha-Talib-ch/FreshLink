"""Stage 8b: problem reports - who may report what, duplicates, admin review and resolution."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import UserRole, VerificationStatus
from app.models.audit import AuditLog
from tests.api.test_assignments import _volunteer, world  # noqa: F401  (fixture)
from tests.api.test_notifications import _receiver, _request, food, scene  # noqa: F401  (fixtures)
from tests.conftest import MakeUser, login_headers

APPROVED = VerificationStatus.APPROVED
URL = "/api/v1/issues"


@pytest.fixture
def admin(client: TestClient, make_user: MakeUser) -> dict:
    return login_headers(client, make_user(role=UserRole.ADMIN, verification_status=APPROVED).email)


def _report(client: TestClient, headers: dict, entity_type: str, entity_id: str, category: str = "no_show",
            description: str = "Nobody was at the shop at 5 pm."):  # noqa: ANN202
    return client.post(URL, headers=headers, json={"entity_type": entity_type, "entity_id": entity_id,
                                                   "category": category, "description": description})


def test_receiver_reports_a_request(client: TestClient, admin: dict, scene: dict, make_user: MakeUser,
                                    db_session: Session, food: dict) -> None:
    request = _request(client, scene)
    created = _report(client, scene["receiver"], "request", request["id"], "delay", "No answer for two days.")
    assert created.status_code == 201, created.text
    issue = created.json()
    assert (issue["status"], issue["reporter_role"], issue["category"]) == ("open", "receiver", "delay")
    alert = client.get("/api/v1/notifications", headers=admin).json()["items"][0]
    assert alert["title"] == "Problem reported: request" and alert["link"] == f"/issues/{issue['id']}"
    assert "reported 'delay' about Fresh bread for Hope NGO" in alert["body"]

    twice = _report(client, scene["receiver"], "request", request["id"], "delay", "Still no answer, sorry.")
    assert twice.status_code == 409 and twice.json()["error"]["code"] == "DUPLICATE_ISSUE"
    assert _report(client, scene["provider"], "request", request["id"]).status_code == 201  # the other side may too

    _, stranger = _receiver(client, make_user, db_session, food, "Other NGO")
    assert _report(client, stranger, "request", request["id"]).status_code == 404  # not involved: not found
    assert _report(client, scene["receiver"], "request", str(uuid.uuid4())).status_code == 404
    assert _report(client, admin, "request", request["id"]).status_code == 403  # admins resolve, not report
    short = _report(client, scene["receiver"], "listing", scene["listing"]["id"], description="bad")
    assert short.status_code == 422
    unverified = login_headers(client, make_user(role=UserRole.RECEIVER).email)
    assert _report(client, unverified, "listing", scene["listing"]["id"]).status_code == 403


def test_listing_reports(client: TestClient, scene: dict, make_user: MakeUser, db_session: Session,
                         food: dict) -> None:
    _, other = _receiver(client, make_user, db_session, food, "Other NGO")  # can see the active listing
    made = _report(client, other, "listing", scene["listing"]["id"], "inappropriate", "The photo is not food at all.")
    assert made.status_code == 201
    volunteer = login_headers(client, make_user(role=UserRole.VOLUNTEER, verification_status=APPROVED).email)
    assert _report(client, volunteer, "listing", scene["listing"]["id"]).status_code == 404


def test_collection_and_delivery_reports(client: TestClient, make_user: MakeUser, world: dict) -> None:  # noqa: F811
    _, bilal = _volunteer(client, make_user, "Bilal")
    _, zara = _volunteer(client, make_user, "Zara")
    task, collection = world["task"]["id"], world["collection"]["id"]
    assert _report(client, bilal, "assignment", task).status_code == 404  # not on the task yet
    client.post(f"/api/v1/assignments/{task}/claim", headers=bilal)
    assert _report(client, bilal, "assignment", task, "delay", "Traffic jam on Ferozepur Road.").status_code == 201
    assert _report(client, bilal, "collection", collection, "quantity_mismatch",
                   "Only 12 packages were ready, not 15.").status_code == 201
    assert _report(client, zara, "collection", collection).status_code == 404
    assert _report(client, world["provider"], "assignment", task, "behaviour",
                   "The volunteer was very polite, just testing.").status_code == 201


def test_admin_review_and_resolution(client: TestClient, admin: dict, scene: dict, db_session: Session) -> None:
    request = _request(client, scene)
    issue = _report(client, scene["receiver"], "request", request["id"]).json()
    url = f"/api/v1/admin/issues/{issue['id']}"

    listed = client.get("/api/v1/admin/issues?unresolved_only=true&entity_type=request", headers=admin).json()
    assert [i["id"] for i in listed["items"]] == [issue["id"]] and listed["items"][0]["reporter_name"] == "Test User"
    assert client.patch(url, headers=admin, json={"status": "in_review"}).json()["status"] == "in_review"
    no_note = client.patch(url, headers=admin, json={"status": "resolved"})
    assert no_note.status_code == 422 and no_note.json()["error"]["code"] == "RESOLUTION_NOTE_REQUIRED"

    done = client.patch(url, headers=admin, json={"status": "resolved",
                                                  "resolution_note": "We called the bakery; pickup moved to 6 pm."})
    assert done.status_code == 200
    out = done.json()
    assert out["status"] == "resolved" and out["resolved_by_email"].endswith("@example.com")
    note = client.get("/api/v1/notifications", headers=scene["receiver"]).json()["items"][0]
    assert note["title"] == "Your problem report was resolved" and note["body"].startswith("We called the bakery")
    assert client.patch(url, headers=admin, json={"status": "open"}).status_code == 409  # re-open via in_review
    assert db_session.scalar(select(AuditLog.reason).where(AuditLog.action == "issue.update",
                                                           AuditLog.after["status"].astext == "resolved")) \
        == "We called the bakery; pickup moved to 6 pm."

    reopened = client.patch(url, headers=admin, json={"status": "in_review"}).json()  # re-open
    assert reopened["status"] == "in_review" and reopened["resolved_by_email"] is None
    client.patch(url, headers=admin, json={"status": "resolved", "resolution_note": "Sorted out again."})

    # a resolved report no longer blocks a new one about the same request ...
    assert _report(client, scene["receiver"], "request", request["id"]).status_code == 201
    # ... and then the old one can't be re-opened next to it
    clash = client.patch(url, headers=admin, json={"status": "in_review"})
    assert clash.status_code == 409 and clash.json()["error"]["code"] == "DUPLICATE_ISSUE"
    assert client.patch(url, headers=scene["provider"], json={"status": "in_review"}).status_code == 403


def test_reporters_see_only_their_own(client: TestClient, admin: dict, scene: dict) -> None:
    request = _request(client, scene)
    mine = _report(client, scene["receiver"], "request", request["id"]).json()
    theirs = _report(client, scene["provider"], "request", request["id"]).json()
    assert [i["id"] for i in client.get(URL, headers=scene["receiver"]).json()["items"]] == [mine["id"]]
    assert client.get(f"{URL}/{theirs['id']}", headers=scene["receiver"]).status_code == 404
    assert client.get(f"{URL}/{theirs['id']}", headers=admin).status_code == 200
    assert client.get("/api/v1/admin/issues", headers=admin).json()["total"] == 2
