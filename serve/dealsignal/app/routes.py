from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import false, func, select
from sqlalchemy.orm import Session

from dealsignal.models.company import Company
from dealsignal.models.company_narrative import CompanyNarrative
from dealsignal.models.database import SessionLocal
from dealsignal.models.narrative_delta import NarrativeDelta
from dealsignal.models.pipeline_run import PipelineRun
from dealsignal.models.signal_event import SignalEvent
from dealsignal.models.source import Source

router = APIRouter()
templates = Jinja2Templates(directory="dealsignal/app/templates")
IST = timezone(timedelta(hours=5, minutes=30))


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
    change_statuses: list[str] | None = Query(default=None),
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
    selected_change_statuses = [s.strip().lower() for s in (change_statuses or []) if s and s.strip()]
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
    event_cards = [_event_card_view(event) for event in events]
    if selected_change_statuses:
        event_cards = [card for card in event_cards if card["change_status"] in selected_change_statuses]
    event_cards.sort(
        key=lambda card: (
            {"alert": 0, "recorded": 1, "standard": 2}.get(card["change_status"], 3),
            -card["event"].score,
        )
    )
    alert_count = sum(1 for card in event_cards if card["change_status"] == "alert")
    recorded_count = sum(1 for card in event_cards if card["change_status"] == "recorded")
    standard_count = sum(1 for card in event_cards if card["change_status"] == "standard")
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
            "events": event_cards,
            "latest_run": latest_run,
            "companies": companies,
            "signal_types": signal_type_options,
            "change_status_options": [
                {"value": "alert", "label": "Priority Alerts"},
                {"value": "recorded", "label": "Recorded Changes"},
                {"value": "standard", "label": "Standard Signals"},
            ],
            "selected_company_ids": selected_company_ids,
            "selected_signal_types": selected_signal_types,
            "selected_change_statuses": selected_change_statuses,
            "selected_min_score": selected_min_score,
            "selected_date_from": selected_date_from,
            "feed_counts": {
                "alert": alert_count,
                "recorded": recorded_count,
                "standard": standard_count,
            },
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
    recent_deltas = db.scalars(
        select(NarrativeDelta)
        .where(NarrativeDelta.company_id == company_id)
        .order_by(NarrativeDelta.created_at.desc())
        .limit(10)
    ).all()
    recent_deltas_view = [_delta_to_view(delta) for delta in recent_deltas]
    recent_deltas_view.sort(
        key=lambda item: (
            0 if item and item["should_alert"] else 1,
            -(item["significance_score"] if item else 0),
        )
    )
    narrative = db.scalar(select(CompanyNarrative).where(CompanyNarrative.company_id == company_id))
    events_view = [{"event": event, "delta_view": _delta_to_view(event.narrative_delta)} for event in events]
    events_view.sort(
        key=lambda item: (
            {"alert": 0, "recorded": 1, "standard": 2}.get(
                "alert"
                if item["delta_view"] and item["delta_view"]["should_alert"]
                else "recorded"
                if item["delta_view"]
                else "standard",
                3,
            ),
            -item["event"].score,
        )
    )
    return templates.TemplateResponse(
        "company_detail.html",
        {
            "request": request,
            "company": company,
            "events": events_view,
            "recent_deltas": recent_deltas_view,
            "narrative": narrative,
        },
    )


@router.get("/events/{event_id}")
def event_detail(event_id: int, request: Request, db: Session = Depends(get_db)):
    event = db.get(SignalEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    delta = db.scalar(select(NarrativeDelta).where(NarrativeDelta.source_event_id == event_id))
    return templates.TemplateResponse(
        "event_detail.html",
        {"request": request, "event": event, "delta": delta, "delta_view": _delta_to_view(delta)},
    )


@router.get("/admin")
def admin(request: Request, db: Session = Depends(get_db)):
    recent_runs = db.scalars(select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(5)).all()
    latest_run = recent_runs[0] if recent_runs else None
    latest_run_view = _run_to_view(latest_run) if latest_run else None
    recent_runs_view = [_run_to_view(run) for run in recent_runs]
    total_runs = int(db.scalar(select(func.count()).select_from(PipelineRun)) or 0)
    total_events = int(db.scalar(select(func.count()).select_from(SignalEvent)) or 0)
    total_sources = int(db.scalar(select(func.count()).select_from(Source)) or 0)
    total_companies = int(db.scalar(select(func.count()).select_from(Company)) or 0)
    total_deltas = int(db.scalar(select(func.count()).select_from(NarrativeDelta)) or 0)
    alert_deltas = int(
        db.scalar(select(func.count()).select_from(NarrativeDelta).where(NarrativeDelta.should_alert.is_(True))) or 0
    )
    fetch_errors = int(
        db.scalar(select(func.count()).select_from(Source).where(Source.status == "fetch_error")) or 0
    )
    extracted_sources = int(
        db.scalar(select(func.count()).select_from(Source).where(Source.status == "extracted")) or 0
    )

    cron_expression = os.getenv("CRON_EXPRESSION", "0 1 * * *")
    azure_meta = {
        "subscription_id": os.getenv("AZURE_SUBSCRIPTION_ID", ""),
        "resource_group": os.getenv("RESOURCE_GROUP", "rg-dealsignal-prod"),
        "environment": os.getenv("ENV_NAME", "dealsignal-env"),
        "job_name": os.getenv("JOB_NAME", "dealsignal-nightly"),
        "acr_name": os.getenv("ACR_NAME", "dealsignalacr12345"),
        "image_name": os.getenv("IMAGE_NAME", "dealsignal-pipeline:latest"),
        "location": os.getenv("LOCATION", "eastus"),
    }
    azure_meta["portal_job_url"] = _portal_job_url(
        subscription_id=azure_meta["subscription_id"],
        resource_group=azure_meta["resource_group"],
        job_name=azure_meta["job_name"],
    )

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "latest_run": latest_run_view,
            "recent_runs": recent_runs_view,
            "cron_expression": cron_expression,
            "azure_meta": azure_meta,
            "timezone_label": "IST (UTC+05:30)",
            "metrics": {
                "total_runs": total_runs,
                "total_events": total_events,
                "total_sources": total_sources,
                "total_companies": total_companies,
                "total_deltas": total_deltas,
                "alert_deltas": alert_deltas,
                "fetch_errors": fetch_errors,
                "extracted_sources": extracted_sources,
            },
        },
    )


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


def _portal_job_url(subscription_id: str, resource_group: str, job_name: str) -> str:
    if not subscription_id or not resource_group or not job_name:
        return ""
    return (
        "https://portal.azure.com/#@/resource/subscriptions/"
        f"{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.App/jobs/{job_name}/overview"
    )


def _to_ist(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")


def _run_to_view(run: PipelineRun) -> dict:
    return {
        "status": run.status,
        "provider": run.provider,
        "watchlist_count": run.watchlist_count,
        "discovered_count": run.discovered_count,
        "fetched_count": run.fetched_count,
        "extracted_count": run.extracted_count,
        "failed_count": run.failed_count,
        "stage_seconds": run.stage_seconds or {},
        "error_message": run.error_message,
        "started_at_ist": _to_ist(run.started_at),
        "ended_at_ist": _to_ist(run.ended_at),
    }


def _delta_to_view(delta: NarrativeDelta | None) -> dict | None:
    if delta is None:
        return None

    payload = delta.delta_payload or {}
    labels = {
        "new_geographies": "New Geography",
        "new_verticals": "New Vertical",
        "new_themes": "New Theme",
        "new_counterparties": "New Counterparty",
        "new_strategy_phrases": "New Strategy Language",
    }
    items = []
    for key, label in labels.items():
        values = payload.get(key) or []
        if values:
            items.append({"label": label, "values": values})

    if items:
        headline = "Alert-worthy change detected" if delta.should_alert else "Change recorded"
    else:
        headline = "No material change detected"

    return {
        "headline": headline,
        "items": items,
        "significance_score": delta.significance_score,
        "should_alert": delta.should_alert,
        "reason": delta.reason,
    }


def _event_card_view(event: SignalEvent) -> dict:
    delta_view = _delta_to_view(event.narrative_delta)
    if delta_view is None:
        change_status = "standard"
    elif delta_view["should_alert"]:
        change_status = "alert"
    else:
        change_status = "recorded"
    return {
        "event": event,
        "delta_view": delta_view,
        "change_status": change_status,
    }
