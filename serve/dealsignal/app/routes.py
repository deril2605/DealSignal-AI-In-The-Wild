from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import false, func, select
from sqlalchemy.orm import Session

from dealsignal.models.company import Company
from dealsignal.models.company_narrative import CompanyNarrative
from dealsignal.models.database import SessionLocal
from dealsignal.models.lead_score import LeadScore
from dealsignal.models.narrative_delta import NarrativeDelta
from dealsignal.models.opportunity_eval import OpportunityEval
from dealsignal.models.pipeline_run import PipelineRun
from dealsignal.models.signal_event import SignalEvent
from dealsignal.models.source import Source
from dealsignal.pipeline.config import ensure_scoring_config, update_scoring_config
from dealsignal.pipeline.evals import top_opportunity_scores
from dealsignal.state_sync import upload_sqlite_to_blob

router = APIRouter()
templates = Jinja2Templates(directory="dealsignal/app/templates")
IST = timezone(timedelta(hours=5, minutes=30))
logger = logging.getLogger(__name__)
PRIORITY_WINDOW_DAYS = 7
PRIORITY_MIN_LEAD_SCORE = 80.0
PRIORITY_MAX_COMPANIES = 8
PRIORITY_MAX_PER_COMPANY = 2


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
    now = datetime.utcnow()

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
    event_cards = [_event_card_view(event, now=now) for event in events]
    if selected_change_statuses:
        event_cards = [card for card in event_cards if card["change_status"] in selected_change_statuses]
    event_cards.sort(
        key=lambda card: (
            {"alert": 0, "recorded": 1, "standard": 2}.get(card["change_status"], 3),
            -card["effective_at_sort_ts"],
            -card["lead_score_value"],
            -card["event"].score,
        )
    )
    priority_now = _priority_now_cards(event_cards)
    curated_priority_ids = {card["event"].id for card in priority_now}
    main_feed_cards = [card for card in event_cards if card["event"].id not in curated_priority_ids]
    alert_count = sum(1 for card in event_cards if card["change_status"] == "alert")
    recorded_count = sum(1 for card in event_cards if card["change_status"] == "recorded")
    standard_count = sum(1 for card in event_cards if card["change_status"] == "standard")
    signal_type_options = db.scalars(
        select(SignalEvent.signal_type).distinct().order_by(SignalEvent.signal_type.asc())
    ).all()
    if not selected_signal_types:
        selected_signal_types = list(signal_type_options)
    latest_run = db.scalars(select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(1)).first()
    top_opportunities = _top_opportunities(db, limit=5)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "events": main_feed_cards,
            "priority_now": priority_now,
            "latest_run": latest_run,
            "top_opportunities": top_opportunities,
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


@router.get("/opportunities")
def opportunities(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "opportunities.html",
        {"request": request, "opportunities": _top_opportunities(db, limit=25)},
    )


@router.get("/evals")
def evals(request: Request, db: Session = Depends(get_db)):
    items = db.scalars(
        select(OpportunityEval).order_by(OpportunityEval.snapshot_date.desc(), OpportunityEval.rank.asc())
    ).all()
    config = ensure_scoring_config(db)
    db.commit()
    grouped = _group_evals(items)
    pending_count = sum(1 for item in items if item.review_status == "pending")
    useful_count = sum(1 for item in items if item.review_status == "useful")
    maybe_count = sum(1 for item in items if item.review_status == "maybe")
    not_useful_count = sum(1 for item in items if item.review_status == "not_useful")
    latest_candidates = [
        {
            "lead_score": _lead_score_to_view(score),
            "event": score.source_event,
            "company": score.company,
            **_display_event_context(score.source_event),
        }
        for score in top_opportunity_scores(db, limit=5)
    ]
    return templates.TemplateResponse(
        "evals.html",
        {
            "request": request,
            "eval_groups": grouped,
            "scoring_config": config,
            "metrics": {
                "pending": pending_count,
                "useful": useful_count,
                "maybe": maybe_count,
                "not_useful": not_useful_count,
            },
            "analytics": _eval_analytics(items),
            "latest_candidates": latest_candidates,
        },
    )


@router.post("/evals/config")
def update_eval_config(
    alert_threshold: float = Form(...),
    lead_change_weight: float = Form(...),
    lead_strength_weight: float = Form(...),
    lead_recency_weight: float = Form(...),
    lead_reinforcement_weight: float = Form(...),
    lead_thesis_weight: float = Form(...),
    lead_source_weight: float = Form(...),
    db: Session = Depends(get_db),
):
    weights = {
        "lead_change_weight": _bounded_float(lead_change_weight, 0.0, 1.0),
        "lead_strength_weight": _bounded_float(lead_strength_weight, 0.0, 1.0),
        "lead_recency_weight": _bounded_float(lead_recency_weight, 0.0, 1.0),
        "lead_reinforcement_weight": _bounded_float(lead_reinforcement_weight, 0.0, 1.0),
        "lead_thesis_weight": _bounded_float(lead_thesis_weight, 0.0, 1.0),
        "lead_source_weight": _bounded_float(lead_source_weight, 0.0, 1.0),
    }
    total_weight = sum(weights.values())
    if total_weight <= 0:
        raise HTTPException(status_code=400, detail="At least one lead-score weight must be positive.")
    normalized = {key: round(value / total_weight, 4) for key, value in weights.items()}
    update_scoring_config(
        db,
        alert_threshold=_bounded_float(alert_threshold, 0.0, 1.0),
        **normalized,
    )
    db.commit()
    upload_sqlite_to_blob(logger=logger)
    return RedirectResponse(url="/evals", status_code=303)


@router.post("/evals/{eval_id}")
def update_eval(
    eval_id: int,
    review_status: str = Form(...),
    review_notes: str = Form(default=""),
    db: Session = Depends(get_db),
):
    item = db.get(OpportunityEval, eval_id)
    if not item:
        raise HTTPException(status_code=404, detail="Eval item not found")
    normalized_status = review_status.strip().lower()
    allowed = {"useful", "maybe", "not_useful"}
    if normalized_status not in allowed:
        raise HTTPException(status_code=400, detail="Invalid review status")
    item.review_status = normalized_status
    item.review_notes = review_notes.strip()
    item.reviewed_at = datetime.utcnow()
    db.commit()
    upload_sqlite_to_blob(logger=logger)
    return RedirectResponse(url="/evals", status_code=303)


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
    events_view = [_event_card_view(event) for event in events]
    events_view.sort(
        key=lambda item: (
            {"alert": 0, "recorded": 1, "standard": 2}.get(
                item["change_status"],
                3,
            ),
            -item["effective_at_sort_ts"],
            -item["lead_score_value"],
            -item["event"].score,
        )
    )
    top_company_opportunities = sorted(
        [item for item in events_view if item["lead_score_view"]],
        key=lambda item: (-item["lead_score_value"], -item["event"].score),
    )[:5]
    return templates.TemplateResponse(
        "company_detail.html",
        {
            "request": request,
            "company": company,
            "events": events_view,
            "top_company_opportunities": top_company_opportunities,
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
    lead_score = db.scalar(select(LeadScore).where(LeadScore.source_event_id == event_id))
    display = _display_event_context(event)
    return templates.TemplateResponse(
        "event_detail.html",
        {
            "request": request,
            "event": event,
            "delta": delta,
            "delta_view": _delta_to_view(delta),
            "lead_score_view": _lead_score_to_view(lead_score),
            "display_title": display["title"],
            "display_subtitle": display["subtitle"],
        },
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
    total_lead_scores = int(db.scalar(select(func.count()).select_from(LeadScore)) or 0)
    total_evals = int(db.scalar(select(func.count()).select_from(OpportunityEval)) or 0)
    useful_evals = int(
        db.scalar(select(func.count()).select_from(OpportunityEval).where(OpportunityEval.review_status == "useful")) or 0
    )
    maybe_evals = int(
        db.scalar(select(func.count()).select_from(OpportunityEval).where(OpportunityEval.review_status == "maybe")) or 0
    )
    not_useful_evals = int(
        db.scalar(select(func.count()).select_from(OpportunityEval).where(OpportunityEval.review_status == "not_useful")) or 0
    )
    pending_evals = int(
        db.scalar(select(func.count()).select_from(OpportunityEval).where(OpportunityEval.review_status == "pending")) or 0
    )
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
                "total_lead_scores": total_lead_scores,
                "total_evals": total_evals,
                "pending_evals": pending_evals,
                "useful_evals": useful_evals,
                "maybe_evals": maybe_evals,
                "not_useful_evals": not_useful_evals,
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


def _lead_score_to_view(score: LeadScore | None) -> dict | None:
    if score is None:
        return None
    components = [
        {"label": "Lead", "value": score.lead_score},
        {"label": "Change", "value": score.change_significance_score},
        {"label": "Strength", "value": score.signal_strength_score},
        {"label": "Recency", "value": score.recency_score},
        {"label": "Reinforcement", "value": score.reinforcement_score},
        {"label": "Thesis Fit", "value": score.thesis_fit_score},
        {"label": "Source", "value": score.source_quality_score},
    ]
    if score.relationship_score > 0:
        components.append({"label": "Relationship", "value": score.relationship_score})
    return {
        "lead_score": score.lead_score,
        "components": components,
        "explanation": score.explanation,
    }


def _event_card_view(event: SignalEvent, now: datetime | None = None) -> dict:
    delta_view = _delta_to_view(event.narrative_delta)
    lead_score_view = _lead_score_to_view(event.lead_score)
    display = _display_event_context(event)
    effective_at = _event_effective_at(event)
    change_status = _change_status_for_feed(
        event=event,
        delta_view=delta_view,
        lead_score_view=lead_score_view,
        now=now,
    )
    return {
        "event": event,
        "delta_view": delta_view,
        "lead_score_view": lead_score_view,
        "lead_score_value": lead_score_view["lead_score"] if lead_score_view else 0.0,
        "change_status": change_status,
        "effective_at": effective_at,
        "effective_at_sort_ts": effective_at.timestamp() if effective_at else 0.0,
        "display_title": display["title"],
        "display_subtitle": display["subtitle"],
    }


def _change_status_for_feed(
    event: SignalEvent,
    delta_view: dict | None,
    lead_score_view: dict | None,
    now: datetime | None = None,
) -> str:
    if delta_view is None:
        return "standard"
    if not delta_view["should_alert"]:
        return "recorded"

    current_time = now or datetime.utcnow()
    effective_at = _event_effective_at(event)
    if effective_at is None:
        return "recorded"
    if current_time - effective_at > timedelta(days=PRIORITY_WINDOW_DAYS):
        return "recorded"

    lead_score = lead_score_view["lead_score"] if lead_score_view else 0.0
    if lead_score < PRIORITY_MIN_LEAD_SCORE:
        return "recorded"
    return "alert"


def _event_effective_at(event: SignalEvent) -> datetime | None:
    return event.source.published_at or event.source.discovered_at or event.created_at


def _priority_now_cards(event_cards: list[dict]) -> list[dict]:
    curated: list[dict] = []
    company_counts: dict[int, int] = {}
    for card in event_cards:
        if card["change_status"] != "alert":
            continue
        company_id = card["event"].company_id
        if company_counts.get(company_id, 0) >= PRIORITY_MAX_PER_COMPANY:
            continue
        curated.append(card)
        company_counts[company_id] = company_counts.get(company_id, 0) + 1
        if len(curated) >= PRIORITY_MAX_COMPANIES:
            break
    return curated


def _top_opportunities(db: Session, limit: int) -> list[dict]:
    scores = top_opportunity_scores(db, limit=limit)
    return [
        {
            "lead_score": _lead_score_to_view(score),
            "event": score.source_event,
            "company": score.company,
            "delta_view": _delta_to_view(score.narrative_delta),
            **_display_event_context(score.source_event),
        }
        for score in scores
    ]


def _group_evals(items: list[OpportunityEval]) -> list[dict]:
    groups: list[dict] = []
    current_date = None
    current_items: list[dict] = []
    for item in items:
        if item.snapshot_date != current_date:
            if current_date is not None:
                groups.append(
                    {
                        "snapshot_date": current_date.isoformat(),
                        "items": current_items,
                        "counts": _eval_counts(current_items),
                    }
                )
            current_date = item.snapshot_date
            current_items = []
        current_items.append(
            {
                **_display_event_context(item.signal_event),
                "id": item.id,
                "rank": item.rank,
                "status": item.review_status,
                "notes": item.review_notes,
                "reviewed_at_ist": _to_ist(item.reviewed_at),
                "lead_score_value": item.lead_score_value,
                "explanation": item.explanation,
                "company": item.company,
                "event": item.signal_event,
            }
        )
    if current_date is not None:
        groups.append(
            {
                "snapshot_date": current_date.isoformat(),
                "items": current_items,
                "counts": _eval_counts(current_items),
            }
        )
    return groups


def _eval_counts(items: list[dict]) -> dict[str, int]:
    return {
        "pending": sum(1 for item in items if item["status"] == "pending"),
        "useful": sum(1 for item in items if item["status"] == "useful"),
        "maybe": sum(1 for item in items if item["status"] == "maybe"),
        "not_useful": sum(1 for item in items if item["status"] == "not_useful"),
    }


def _eval_analytics(items: list[OpportunityEval]) -> dict[str, object]:
    reviewed = [item for item in items if item.review_status != "pending"]
    reviewed_count = len(reviewed)
    useful_count = sum(1 for item in reviewed if item.review_status == "useful")
    maybe_count = sum(1 for item in reviewed if item.review_status == "maybe")
    not_useful_count = sum(1 for item in reviewed if item.review_status == "not_useful")
    useful_rate = round((useful_count / reviewed_count) * 100, 1) if reviewed_count else 0.0
    maybe_rate = round((maybe_count / reviewed_count) * 100, 1) if reviewed_count else 0.0
    not_useful_rate = round((not_useful_count / reviewed_count) * 100, 1) if reviewed_count else 0.0
    by_company: dict[str, dict[str, int]] = {}
    for item in reviewed:
        bucket = by_company.setdefault(item.company.name, {"useful": 0, "maybe": 0, "not_useful": 0})
        bucket[item.review_status] += 1
    company_rows = sorted(
        (
            {
                "company": name,
                "useful": counts["useful"],
                "maybe": counts["maybe"],
                "not_useful": counts["not_useful"],
            }
            for name, counts in by_company.items()
        ),
        key=lambda row: (-row["useful"], row["not_useful"], row["company"]),
    )
    return {
        "reviewed_count": reviewed_count,
        "useful_rate": useful_rate,
        "maybe_rate": maybe_rate,
        "not_useful_rate": not_useful_rate,
        "by_company": company_rows,
    }


def _bounded_float(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))


def _display_event_context(event: SignalEvent) -> dict[str, str]:
    fields = event.extracted_fields or {}
    counterparties = [
        name for name in (fields.get("counterparties") or []) if str(name).strip() and str(name).strip().lower() != event.company.name.lower()
    ]
    themes = [str(name).strip() for name in (fields.get("themes") or []) if str(name).strip()]
    signal_type = event.signal_type
    company_name = event.company.name

    if signal_type == "M&A / Acquisition Intent":
        if counterparties:
            return {
                "title": f"{company_name} acquisition signal involving {', '.join(counterparties[:2])}",
                "subtitle": "Counterparty surfaced from extracted fields.",
            }
        return {"title": f"{company_name} acquisition signal", "subtitle": "No explicit counterparty extracted."}

    if signal_type == "Strategic Partnership":
        if counterparties:
            return {
                "title": f"{company_name} partnership signal with {', '.join(counterparties[:2])}",
                "subtitle": "Counterparty surfaced from extracted fields.",
            }
        return {"title": f"{company_name} partnership signal", "subtitle": "No explicit counterparty extracted."}

    if signal_type == "Fundraising / Capital Raise":
        if counterparties:
            return {
                "title": f"{company_name} fundraising signal involving {', '.join(counterparties[:2])}",
                "subtitle": "Counterparty surfaced from extracted fields.",
            }
        return {"title": f"{company_name} fundraising signal", "subtitle": "No explicit counterparty extracted."}

    if signal_type == "Product Expansion" and counterparties:
        return {
            "title": f"{company_name} product expansion signal linked to {', '.join(counterparties[:2])}",
            "subtitle": "Counterparty surfaced from extracted fields.",
        }

    if themes:
        return {"title": f"{company_name} {signal_type.lower()}", "subtitle": f"Top theme: {themes[0]}"}

    return {"title": f"{company_name} {signal_type.lower()}", "subtitle": ""}
