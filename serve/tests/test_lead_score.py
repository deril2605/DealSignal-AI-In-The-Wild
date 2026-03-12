from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dealsignal.models.company import Company
from dealsignal.models.database import Base
from dealsignal.models.narrative_delta import NarrativeDelta
from dealsignal.models.signal_event import SignalEvent
from dealsignal.models.source import Source
from dealsignal.pipeline.discover import WatchlistEntity
from dealsignal.pipeline.lead_score import evaluate_lead_score


def test_lead_score_rewards_thesis_fit(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, future=True, expire_on_commit=False)

    monkeypatch.setattr(
        "dealsignal.pipeline.lead_score._watchlist_lookup",
        lambda: {
            "fitco": WatchlistEntity(
                name="FitCo",
                execs=[],
                themes=["enterprise automation", "AI workflow expansion"],
                aliases=[],
                sector="Enterprise Software",
            )
        },
    )

    with TestSession() as session:
        company = Company(name="FitCo", aliases=[], sector="Enterprise Software")
        session.add(company)
        session.flush()
        source = Source(company_id=company.id, url="https://reuters.com/fitco", status="fetched")
        session.add(source)
        session.flush()
        event = SignalEvent(
            company_id=company.id,
            source_id=source.id,
            signal_type="Strategic Partnership",
            summary="FitCo launched an AI workflow automation partnership for enterprise operations teams.",
            evidence_excerpt="Evidence",
            extracted_fields={
                "geography": ["US"],
                "counterparties": ["PartnerOne"],
                "themes": ["AI workflow expansion", "enterprise automation"],
            },
            confidence=0.9,
            strength=0.9,
            score=88.0,
            event_fingerprint="fitco-score",
        )
        session.add(event)
        session.flush()
        delta = NarrativeDelta(
            company_id=company.id,
            source_event_id=event.id,
            delta_type=["new_themes"],
            delta_payload={"new_themes": ["AI workflow expansion"]},
            significance_score=0.8,
            should_alert=True,
            reason="Alert-worthy change detected",
        )
        session.add(delta)
        session.flush()

        lead_score = evaluate_lead_score(session, event, delta)

    assert lead_score.lead_score > 70
    assert lead_score.thesis_fit_score >= 80
    assert lead_score.source_quality_score >= 90


def test_lead_score_rewards_reinforcement(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, future=True, expire_on_commit=False)

    monkeypatch.setattr("dealsignal.pipeline.lead_score._watchlist_lookup", lambda: {})

    with TestSession() as session:
        company = Company(name="RepeatCo", aliases=[], sector=None)
        session.add(company)
        session.flush()

        prior_source_a = Source(
            company_id=company.id,
            url="https://reuters.com/repeat-a",
            status="fetched",
            discovered_at=datetime.utcnow() - timedelta(days=2),
        )
        prior_source_b = Source(
            company_id=company.id,
            url="https://bloomberg.com/repeat-b",
            status="fetched",
            discovered_at=datetime.utcnow() - timedelta(days=1),
        )
        session.add_all([prior_source_a, prior_source_b])
        session.flush()

        prior_event_a = SignalEvent(
            company_id=company.id,
            source_id=prior_source_a.id,
            signal_type="Strategic Partnership",
            summary="RepeatCo expanded its partner ecosystem.",
            evidence_excerpt="Evidence",
            extracted_fields={"geography": [], "counterparties": ["Alpha"], "themes": ["partner ecosystem"]},
            confidence=0.7,
            strength=0.7,
            score=70.0,
            event_fingerprint="prior-a",
            created_at=datetime.utcnow() - timedelta(days=2),
        )
        prior_event_b = SignalEvent(
            company_id=company.id,
            source_id=prior_source_b.id,
            signal_type="Strategic Partnership",
            summary="RepeatCo added a second ecosystem partner.",
            evidence_excerpt="Evidence",
            extracted_fields={"geography": [], "counterparties": ["Beta"], "themes": ["partner ecosystem"]},
            confidence=0.72,
            strength=0.74,
            score=71.0,
            event_fingerprint="prior-b",
            created_at=datetime.utcnow() - timedelta(days=1),
        )
        session.add_all([prior_event_a, prior_event_b])
        session.flush()

        source = Source(company_id=company.id, url="https://wsj.com/repeat-c", status="fetched")
        session.add(source)
        session.flush()
        event = SignalEvent(
            company_id=company.id,
            source_id=source.id,
            signal_type="Strategic Partnership",
            summary="RepeatCo signed another ecosystem partner for distribution.",
            evidence_excerpt="Evidence",
            extracted_fields={"geography": [], "counterparties": ["Gamma"], "themes": ["partner ecosystem"]},
            confidence=0.85,
            strength=0.85,
            score=85.0,
            event_fingerprint="current",
        )
        session.add(event)
        session.flush()
        delta = NarrativeDelta(
            company_id=company.id,
            source_event_id=event.id,
            delta_type=["new_counterparties"],
            delta_payload={"new_counterparties": ["Gamma"]},
            significance_score=0.75,
            should_alert=True,
            reason="Alert-worthy change detected",
        )
        session.add(delta)
        session.flush()

        lead_score = evaluate_lead_score(session, event, delta)

    assert lead_score.reinforcement_score >= 75
    assert lead_score.lead_score > 60
