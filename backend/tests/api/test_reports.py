"""Stage 8c: reports, exports, fairness and the audit-log viewer.

Scene: the spec 5.5 example (30 packages; NGO 15, kitchen 10, family 10 -> 15 / 10 / 5) plus a
12.5 kg biryani listing that is closed without being requested. The NGO's food is handed over (15)
and received (14); the kitchen's is handed over only (10); the family's is still waiting."""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import UserRole, VerificationStatus
from app.models.audit import AuditLog
from app.models.category import FoodCategory
from app.services.report_service import PKT
from tests.api.test_allocation import _confirm, _suggest, food, scenario  # noqa: F401  (fixtures)
from tests.api.test_listings import _create, biryani
from tests.conftest import MakeUser, login_headers

R = "/api/v1/admin/reports"


@pytest.fixture
def admin(client: TestClient, make_user: MakeUser) -> dict:
    return login_headers(client, make_user(role=UserRole.ADMIN, verification_status=VerificationStatus.APPROVED).email)


@pytest.fixture
def flow(client: TestClient, scenario: dict, food: dict) -> dict:  # noqa: F811
    s = scenario
    run = _suggest(client, s["provider"], s["listing"]["id"])
    assert _confirm(client, s["provider"], run["id"]).status_code == 200
    collections = {c["receiver"]["receiver_name"]: c["id"]
                   for c in client.get("/api/v1/collections", headers=s["provider"]).json()["items"]}
    ngo, kitchen = collections["Hope NGO"], collections["Model Town Kitchen"]
    client.post(f"/api/v1/collections/{ngo}/handover", headers=s["provider"], json={"quantity": 15})
    client.post(f"/api/v1/collections/{ngo}/receipt", headers=s["people"]["ngo"][1], json={"quantity": 14})
    client.post(f"/api/v1/collections/{kitchen}/handover", headers=s["provider"], json={"quantity": 10})
    leftover = _create(client, s["provider"], biryani(food))
    client.post(f"/api/v1/listings/{leftover['id']}/close", headers=s["provider"])
    typo = _create(client, s["provider"], biryani(food, food_name="Typo", total_quantity=99))
    assert client.delete(f"/api/v1/listings/{typo['id']}", headers=s["provider"]).status_code == 204  # never counted
    return s


def _get(client: TestClient, admin: dict, path: str) -> dict:
    response = client.get(f"{R}/{path}", headers=admin)
    assert response.status_code == 200, response.text
    return response.json()


def _q(items: list[dict]) -> dict[str, str]:
    return {i["unit"]: i["quantity"] for i in items}


def test_summary_keeps_the_four_quantities_apart(client: TestClient, admin: dict, flow: dict) -> None:
    s = _get(client, admin, "summary")
    assert (s["listings"], s["requests"], s["allocations"]) == (2, 3, 3)
    assert s["requests_by_status"]["completed"] == 1 and s["requests_by_status"]["approved"] == 1
    assert s["requests_by_status"]["partially_approved"] == 1
    assert (s["successful_redistributions"], s["failed_collections"]) == (1, 0)
    assert _q(s["quantity_listed"]) == {"kg": "12.50", "package": "30.00"}
    assert _q(s["quantity_allocated"]) == {"package": "30.00"}
    assert _q(s["quantity_collected"]) == {"package": "25.00"}  # handed over: 15 + 10
    assert _q(s["quantity_distributed"]) == {"package": "14.00"}  # confirmed received
    assert _q(s["quantity_not_redistributed"]) == {"kg": "12.50"}  # the closed biryani


def test_filters(client: TestClient, admin: dict, flow: dict, db_session: Session) -> None:
    yesterday = (date.today() - timedelta(days=2)).isoformat()
    old = _get(client, admin, f"summary?date_to={yesterday}")
    assert old["listings"] == 0 and old["quantity_listed"] == []
    today = datetime.now(PKT).date().isoformat()  # date_to includes its own day (Pakistan time)
    assert _get(client, admin, f"summary?date_from={today}&date_to={today}")["listings"] == 2
    bakery = db_session.scalar(select(FoodCategory.id).where(FoodCategory.code == "bakery_items"))
    only_bread = _get(client, admin, f"summary?food_category_id={bakery}")
    assert _q(only_bread["quantity_listed"]) == {"package": "30.00"}
    assert _get(client, admin, "summary?listing_status=closed")["listings"] == 1
    assert _get(client, admin, "summary?request_status=pending")["requests"] == 0
    ngo = client.get("/api/v1/receivers/me", headers=flow["people"]["ngo"][1]).json()["id"]
    mine = _get(client, admin, f"summary?receiver_id={ngo}")
    assert (mine["requests"], _q(mine["quantity_distributed"])) == (1, {"package": "14.00"})
    bad = client.get(f"{R}/summary?date_from=2026-05-02&date_to=2026-05-01", headers=admin)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_DATE_RANGE"
    assert client.get(f"{R}/summary", headers=flow["provider"]).status_code == 403


def test_timeseries(client: TestClient, admin: dict, flow: dict) -> None:
    month = _get(client, admin, "timeseries?interval=month")
    this = month["rows"][-1]
    assert this["period_start"] == date.today().replace(day=1).isoformat()
    assert (this["listings"], this["requests"], this["completed_distributions"]) == (2, 3, 1)
    assert _q(this["quantity_distributed"]) == {"package": "14.00"}
    start = (date.today() - timedelta(days=70)).isoformat()
    filled = _get(client, admin, f"timeseries?interval=month&date_from={start}")["rows"]
    assert len(filled) >= 3 and filled[0]["listings"] == 0  # empty months are included (for charts)
    weeks = _get(client, admin, f"timeseries?interval=week&date_from={start}")["rows"]
    assert all(date.fromisoformat(w["period_start"]).weekday() == 0 for w in weeks)  # weeks start on Monday
    assert sum(w["listings"] for w in weeks) == 2


def test_grouped_reports(client: TestClient, admin: dict, flow: dict) -> None:
    cats = _get(client, admin, "categories")["rows"]
    assert [c["name"] for c in cats][:1] == ["Bakery items"] and cats[0]["requests"] == 3
    provider = _get(client, admin, "providers")["rows"]
    assert len(provider) == 1 and (provider[0]["listings"], provider[0]["completed_distributions"]) == (2, 1)
    receivers = {r["name"]: r for r in _get(client, admin, "receivers")["rows"]}
    assert set(receivers) == {"Hope NGO", "Model Town Kitchen", "Raza Family"}
    assert _q(receivers["Hope NGO"]["quantity_received"]) == {"package": "14.00"}
    assert receivers["Raza Family"]["approved_requests"] == 1 and receivers["Raza Family"]["completed_collections"] == 0
    assert _get(client, admin, "volunteers")["rows"] == []  # no delivery tasks in this scene


def test_fairness(client: TestClient, admin: dict, flow: dict) -> None:
    f = client.get("/api/v1/admin/fairness", headers=admin).json()
    by = {c["name"]: c for c in f["by_receiver_category"]}
    assert len(by) == 3
    ratios = sorted(c["average_fill_ratio"] for c in by.values())
    assert ratios == [0.5, 1.0, 1.0]  # the family got 5 of 10
    ngo = next(c for c in by.values() if c["quantity_received"])
    assert ngo["quantity_received"][0]["share"] == 1.0  # all received packages so far went to the NGO
    gini = {g["unit"]: g for g in f["inequality_by_unit"]}
    assert gini["package"]["receivers"] == 3 and gini["package"]["gini"] == 0.6667  # 14, 0, 0 received
    assert (f["allocations"], f["overridden_allocations"], f["override_rate"]) == (3, 0, 0.0)


def test_csv_export(client: TestClient, admin: dict, flow: dict, db_session: Session) -> None:
    response = client.get(f"{R}/export?report=summary&format=csv", headers=admin)
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    assert 'filename="freshlink-summary-' in response.headers["content-disposition"]
    text = response.content.decode("utf-8-sig")
    assert text.splitlines()[0] == "measure,value"
    assert "quantity distributed,14 packages" in text
    assert "quantity listed,12.5 kg; 30 packages" in text  # per unit, never added together
    entry = db_session.scalar(select(AuditLog).where(AuditLog.action == "report.export"))
    assert entry.reason == "summary" and entry.after["format"] == "csv"

    receivers = client.get(f"{R}/export?report=receivers&format=csv", headers=admin).content.decode("utf-8-sig")
    assert receivers.splitlines()[0].startswith("name,category,requests") and "receiver_id" not in receivers
    trend = client.get(f"{R}/export?report=timeseries&format=csv&interval=week", headers=admin)
    assert trend.status_code == 200 and "period start" in trend.text.splitlines()[0]


def test_pdf_export(client: TestClient, admin: dict, flow: dict, make_user: MakeUser) -> None:
    for report in ("summary", "categories", "providers", "receivers", "volunteers", "fairness", "timeseries"):
        response = client.get(f"{R}/export?report={report}&format=pdf", headers=admin)
        assert response.status_code == 200, (report, response.text)
        assert response.headers["content-type"] == "application/pdf" and response.content.startswith(b"%PDF")
    # an Urdu organisation name must not break the PDF (its letters are replaced, the CSV keeps them)
    client.put("/api/v1/receivers/me", headers=flow["people"]["ngo"][1], json={
        "receiver_name": "امید Hope NGO", "receiver_category_id": client.get(
            "/api/v1/receivers/me", headers=flow["people"]["ngo"][1]).json()["category"]["id"],
        "address": "Model Town, Lahore", "latitude": 31.48, "longitude": 74.32})
    pdf = client.get(f"{R}/export?report=receivers&format=pdf", headers=admin)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert "امید" in client.get(f"{R}/export?report=receivers&format=csv", headers=admin).content.decode("utf-8-sig")
    assert client.get(f"{R}/export?report=nonsense", headers=admin).status_code == 422


def test_audit_log_viewer(client: TestClient, admin: dict, flow: dict) -> None:
    url = "/api/v1/admin/audit-logs"
    client.get(f"{R}/export?report=summary", headers=admin)
    client.get(f"{R}/export?report=providers&format=pdf", headers=admin)
    client.post("/api/v1/admin/announcements", headers=admin, json={"title": "Eid hours", "body": "Closed on Eid day."})
    page = client.get(f"{url}?action=report.", headers=admin).json()  # a prefix
    assert page["total"] == 2 and all(i["action"] == "report.export" for i in page["items"])
    exact = client.get(f"{url}?action=announcement.send", headers=admin).json()
    assert exact["total"] == 1 and exact["items"][0]["actor_role"] == "admin"
    assert exact["items"][0]["after"]["recipients"] >= 4
    me = client.get("/api/v1/auth/me", headers=admin).json()["email"]
    assert client.get(f"{url}?actor={me.split('@')[0]}", headers=admin).json()["total"] == 3
    assert client.get(f"{url}?actor=nobody-here", headers=admin).json()["total"] == 0
    assert client.get(f"{url}?date_to={(date.today() - timedelta(days=2)).isoformat()}",
                      headers=admin).json()["total"] == 0
    newest = client.get(url, headers=admin).json()["items"]
    assert [i["created_at"] for i in newest] == sorted((i["created_at"] for i in newest), reverse=True)
    assert client.get(url, headers=flow["provider"]).status_code == 403
