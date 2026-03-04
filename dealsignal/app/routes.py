from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from dealsignal.models.company import Company
from dealsignal.models.database import SessionLocal
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
def home(request: Request, db: Session = Depends(get_db)):
    events = db.scalars(select(SignalEvent).order_by(SignalEvent.score.desc()).limit(50)).all()
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "events": events},
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

