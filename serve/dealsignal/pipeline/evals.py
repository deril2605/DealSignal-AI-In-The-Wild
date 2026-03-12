from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from dealsignal.models.lead_score import LeadScore
from dealsignal.models.opportunity_eval import OpportunityEval
from dealsignal.models.signal_event import SignalEvent

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


def store_daily_eval_snapshot(session: Session, limit: int = 10) -> int:
    snapshot_date = datetime.now(IST).date()
    existing = session.scalar(
        select(OpportunityEval.id).where(OpportunityEval.snapshot_date == snapshot_date).limit(1)
    )
    if existing is not None:
        logger.info("Daily eval snapshot already exists for %s", snapshot_date.isoformat())
        return 0

    candidates = top_opportunity_scores(session, limit=limit)
    created = 0
    for rank, score in enumerate(candidates, start=1):
        session.add(
            OpportunityEval(
                snapshot_date=snapshot_date,
                rank=rank,
                company_id=score.company_id,
                signal_event_id=score.source_event_id,
                lead_score_id=score.id,
                lead_score_value=score.lead_score,
                explanation=score.explanation,
            )
        )
        created += 1
    if created:
        session.flush()
    logger.info("Stored %s daily eval snapshot rows for %s", created, snapshot_date.isoformat())
    return created


def top_opportunity_scores(session: Session, limit: int) -> list[LeadScore]:
    since = datetime.utcnow() - timedelta(days=7)
    return session.scalars(
        select(LeadScore)
        .join(SignalEvent, LeadScore.source_event_id == SignalEvent.id)
        .where(SignalEvent.created_at >= since)
        .order_by(LeadScore.lead_score.desc(), SignalEvent.created_at.desc())
        .limit(limit)
    ).all()
