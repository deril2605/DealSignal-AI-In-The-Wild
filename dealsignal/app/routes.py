from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import false, select
from sqlalchemy.orm import Session

from dealsignal.models.company import Company
from dealsignal.models.database import SessionLocal
from dealsignal.models.pipeline_run import PipelineRun
from dealsignal.models.signal_event import SignalEvent

router = APIRouter()
templates = Jinja2Templates(directory="dealsignal/app/templates")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/")
def home(
    request: Request,
    company_ids: list[int] | None = Query(default=None),
    company_filter_active: int | None = Query(default=None),
    signal_types: list[str] | None = Query(default=None),
    min_score: float = Query(default=0.0),
    date_from: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    companies = db.scalars(select(Company).order_by(Company.name.asc())).all()
    all_company_ids = [company.id for company in companies]

    selected_company_ids = [c for c in (company_ids or []) if isinstance(c, int)]
    is_company_filter_active = company_filter_active == 1
    if not selected_company_ids and not is_company_filter_active:
        selected_company_ids = all_company_ids

    selected_signal_types = [s.strip() for s in (signal_types or []) if s and s.strip()]
    selected_min_score = max(0.0, min(100.0, float(min_score)))
    selected_date_from = (date_from or "").strip()

    stmt = select(SignalEvent)
    if is_company_filter_active:
        if selected_company_ids:
            stmt = stmt.where(SignalEvent.company_id.in_(selected_company_ids))
        else:
            stmt = stmt.where(false())

    if selected_signal_types:
        stmt = stmt.where(SignalEvent.signal_type.in_(selected_signal_types))
    if selected_min_score > 0:
        stmt = stmt.where(SignalEvent.score >= selected_min_score)

    parsed_from = _parse_date(selected_date_from)
    if parsed_from:
        stmt = stmt.where(SignalEvent.created_at >= parsed_from)

    events = db.scalars(stmt.order_by(SignalEvent.score.desc()).limit(100)).all()
    signal_type_options = db.scalars(
        select(SignalEvent.signal_type).distinct().order_by(SignalEvent.signal_type.asc())
    ).all()
    if not selected_signal_types:
        selected_signal_types = list(signal_type_options)
    latest_run = db.scalars(select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(1)).first()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "events": events,
            "latest_run": latest_run,
            "companies": companies,
            "signal_types": signal_type_options,
            "selected_company_ids": selected_company_ids,
            "selected_signal_types": selected_signal_types,
            "selected_min_score": selected_min_score,
            "selected_date_from": selected_date_from,
        },
    )


@router.get("/companies")
def companies(request: Request, db: Session = Depends(get_db)):
    items = db.scalars(select(Company).order_by(Company.name.asc())).all()
    return templates.TemplateResponse("companies.html", {"request": request, "companies": items})


@router.get("/companies/{company_id}")
def company_detail(company_id: int, request: Request, db: Session = Depends(get_db)):
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    events = db.scalars(
        select(SignalEvent)
        .where(SignalEvent.company_id == company_id)
        .order_by(SignalEvent.score.desc())
        .limit(100)
    ).all()
    return templates.TemplateResponse(
        "company_detail.html",
        {"request": request, "company": company, "events": events},
    )


@router.get("/events/{event_id}")
def event_detail(event_id: int, request: Request, db: Session = Depends(get_db)):
    event = db.get(SignalEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return templates.TemplateResponse("event_detail.html", {"request": request, "event": event})


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
