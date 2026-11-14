"""Admin reports, exports, fairness and the audit-log viewer (spec 14 and 4.4.10; plan 4.12).

All reports share the same filters (spec 14.3): date range, food category, provider, receiver,
listing status and request status. Quantities are always per unit."""

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, select

from app.api.deps import AdminUser, Client, DbSession
from app.core.errors import error_responses
from app.domain.enums import ListingStatus, RequestStatus
from app.models.audit import AuditLog
from app.schemas.common import Page
from app.schemas.reports import (
    AuditLogOut,
    CategoryReport,
    FairnessReport,
    ProviderReport,
    ReceiverReport,
    SummaryReport,
    TimeseriesReport,
    VolunteerReport,
)
from app.services import export_service, report_service
from app.services.audit_service import record
from app.services.report_service import Filters, between

router = APIRouter(tags=["admin-reports"])


def report_filters(
    date_from: date | None = None,
    date_to: date | None = None,
    food_category_id: uuid.UUID | None = None,
    provider_id: uuid.UUID | None = None,
    receiver_id: uuid.UUID | None = None,
    listing_status: ListingStatus | None = None,
    request_status: RequestStatus | None = None,
) -> Filters:
    """Dates are calendar days in Pakistan time; date_to is included."""
    return Filters(date_from, date_to, food_category_id, provider_id, receiver_id, listing_status,
                   request_status).check()


ReportFilters = Annotated[Filters, Depends(report_filters)]
Interval = Literal["week", "month"]
R422 = error_responses(422)


@router.get("/reports/summary", response_model=SummaryReport, responses=R422)
def summary(admin: AdminUser, db: DbSession, f: ReportFilters):  # noqa: ANN201
    """Main statistics: listed, allocated, collected and distributed quantities kept apart."""
    return report_service.summary(db, f)


@router.get("/reports/timeseries", response_model=TimeseriesReport, responses=R422)
def timeseries(admin: AdminUser, db: DbSession, f: ReportFilters, interval: Interval = "month"):  # noqa: ANN201
    """Weekly or monthly trends of listings, requests and distributions (empty periods included)."""
    return report_service.timeseries(db, f, interval)


@router.get("/reports/categories", response_model=CategoryReport, responses=R422)
def categories(admin: AdminUser, db: DbSession, f: ReportFilters):  # noqa: ANN201
    """Food categories, most listed first."""
    return report_service.categories(db, f)


@router.get("/reports/providers", response_model=ProviderReport, responses=R422)
def providers(admin: AdminUser, db: DbSession, f: ReportFilters):  # noqa: ANN201
    """Provider activity."""
    return report_service.providers(db, f)


@router.get("/reports/receivers", response_model=ReceiverReport, responses=R422)
def receivers(admin: AdminUser, db: DbSession, f: ReportFilters):  # noqa: ANN201
    """Receiver activity."""
    return report_service.receivers(db, f)


@router.get("/reports/volunteers", response_model=VolunteerReport, responses=R422)
def volunteers(admin: AdminUser, db: DbSession, f: ReportFilters):  # noqa: ANN201
    """Volunteer activity."""
    return report_service.volunteers(db, f)


@router.get("/fairness", response_model=FairnessReport, responses=R422)
def fairness(admin: AdminUser, db: DbSession, f: ReportFilters):  # noqa: ANN201
    """How fairly food was shared: by receiver category, Gini per unit, and the override rate."""
    return report_service.fairness(db, f)


REPORTS = {"summary": ("Summary", report_service.summary), "categories": ("Food categories", report_service.categories),
           "providers": ("Provider activity", report_service.providers),
           "receivers": ("Receiver activity", report_service.receivers),
           "volunteers": ("Volunteer activity", report_service.volunteers),
           "fairness": ("Fairness", report_service.fairness)}


@router.get("/reports/export", responses={**R422, 200: {
    "content": {"text/csv": {}, "application/pdf": {}}, "description": "The report as a file download"}})
def export(  # noqa: ANN201, PLR0913
    admin: AdminUser,
    db: DbSession,
    client: Client,
    f: ReportFilters,
    report: Literal["summary", "timeseries", "categories", "providers", "receivers", "volunteers", "fairness"],
    format: Literal["csv", "pdf"] = "csv",  # noqa: A002
    interval: Interval = "month",
):
    """Download a report as CSV (opens in Excel) or PDF, with the same filters. Audited."""
    if report == "timeseries":
        title, data = f"Trends per {interval}", report_service.timeseries(db, f, interval)
    else:
        title, build = REPORTS[report]
        data = build(db, f)
    header, rows = export_service.table(report, data)
    record(db, actor=admin, action="report.export", entity_type="report", entity_id=None, client=client,
           reason=report, after={"format": format, "filters": f.as_dict(), "rows": len(rows)})
    db.commit()
    stamp = date.today().isoformat()
    if format == "csv":
        body, media = export_service.to_csv(header, rows), "text/csv; charset=utf-8"
    else:
        body, media = export_service.to_pdf(title, f.as_dict(), header, rows), "application/pdf"
    return Response(body, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="freshlink-{report}-{stamp}.{format}"'})


# ------------------------------------------------------------------- audit log


@router.get("/audit-logs", response_model=Page[AuditLogOut], responses=R422)
def audit_logs(  # noqa: ANN201, PLR0913
    admin: AdminUser,
    db: DbSession,
    actor: Annotated[str | None, Query(max_length=254, description="Part of the actor's email")] = None,
    action: Annotated[str | None, Query(max_length=60, description="Exact action, or a prefix like 'listing.'")] = None,
    entity_type: Annotated[str | None, Query(max_length=40)] = None,
    entity_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
):
    """Every recorded administrative action, newest first (spec: "all important administrative
    actions should be recorded"). The log itself can't be changed or deleted (database trigger)."""
    f = Filters(date_from, date_to).check()
    query = select(AuditLog).where(*between(AuditLog.created_at, f))
    if actor:
        query = query.where(AuditLog.actor_email.ilike(f"%{actor.replace('%', '').replace('_', '')}%"))
    if action:
        query = query.where(AuditLog.action.startswith(action, autoescape=True) if action.endswith(".") else AuditLog.action == action)
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if entity_id:
        query = query.where(AuditLog.entity_id == entity_id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = list(db.scalars(query.order_by(AuditLog.created_at.desc(), AuditLog.id)
                            .offset((page - 1) * page_size).limit(page_size)))
    return {"items": items, "total": total, "page": page, "page_size": page_size}

