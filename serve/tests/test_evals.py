from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dealsignal.models.company import Company
from dealsignal.models.database import Base
from dealsignal.models.lead_score import LeadScore
from dealsignal.models.signal_event import SignalEvent
from dealsignal.models.source import Source
from dealsignal.pipeline.evals import store_daily_eval_snapshot


def test_store_daily_eval_snapshot_is_idempotent_for_same_day():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, future=True, expire_on_commit=False)

    with TestSession() as session:
        company = Company(name="EvalCo", aliases=[], sector=None)
        session.add(company)
        session.flush()
        source = Source(company_id=company.id, url="https://example.com/eval", status="fetched")
        session.add(source)
        session.flush()
        event = SignalEvent(
            company_id=company.id,
            source_id=source.id,
            signal_type="Strategic Partnership",
            summary="EvalCo formed a meaningful partnership.",
            evidence_excerpt="Evidence",
            extracted_fields={"geography": ["US"], "counterparties": ["Partner"], "themes": ["partner ecosystem"]},
            confidence=0.8,
            strength=0.82,
            score=81.0,
            event_fingerprint="eval-fp",
            created_at=datetime.utcnow(),
        )
        session.add(event)
        session.flush()
        session.add(
            LeadScore(
                company_id=company.id,
                source_event_id=event.id,
                lead_score=86.0,
                change_significance_score=80.0,
                signal_strength_score=84.0,
                recency_score=100.0,
                reinforcement_score=28.0,
                thesis_fit_score=60.0,
                relationship_score=0.0,
                source_quality_score=58.0,
                explanation="Lead score 86.00 driven by strong strategic intent.",
            )
        )
        session.flush()

        created_first = store_daily_eval_snapshot(session, limit=10)
        created_second = store_daily_eval_snapshot(session, limit=10)

        assert created_first == 1
        assert created_second == 0
