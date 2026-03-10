from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from dealsignal.models.company import Company
from dealsignal.models.database import Base
from dealsignal.models.signal_event import SignalEvent
from dealsignal.models.source import Source
from dealsignal.pipeline.narrative import evaluate_narrative_delta


def test_narrative_delta_alerts_on_new_geography():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, future=True, expire_on_commit=False)

    with TestSession() as session:
        company = Company(name="TestCo", aliases=[], sector=None)
        session.add(company)
        session.flush()
        source = Source(company_id=company.id, url="https://example.com/a", status="fetched")
        session.add(source)
        session.flush()

        baseline = SignalEvent(
            company_id=company.id,
            source_id=source.id,
            signal_type="Strategic Partnership",
            summary="TestCo expanded partnership motion in US enterprise accounts",
            evidence_excerpt="Evidence 1",
            extracted_fields={
                "geography": ["US"],
                "counterparties": ["PartnerA"],
                "themes": ["enterprise expansion"],
            },
            confidence=0.8,
            strength=0.8,
            score=80.0,
            event_fingerprint="baseline",
        )
        session.add(baseline)
        session.flush()
        first_delta = evaluate_narrative_delta(session, baseline)

        next_source = Source(company_id=company.id, url="https://example.com/b", status="fetched")
        session.add(next_source)
        session.flush()
        next_event = SignalEvent(
            company_id=company.id,
            source_id=next_source.id,
            signal_type="Geographic Expansion",
            summary="TestCo is entering India with an enterprise payments strategy",
            evidence_excerpt="Evidence 2",
            extracted_fields={
                "geography": ["India"],
                "counterparties": [],
                "themes": ["payments", "enterprise expansion"],
            },
            confidence=0.9,
            strength=0.85,
            score=90.0,
            event_fingerprint="second",
        )
        session.add(next_event)
        session.flush()
        second_delta = evaluate_narrative_delta(session, next_event)
        first_should_alert = first_delta.should_alert
        second_should_alert = second_delta.should_alert
        second_new_geographies = second_delta.delta_payload["new_geographies"]
        session.commit()

    assert first_should_alert is True
    assert second_should_alert is True
    assert "India" in second_new_geographies


def test_narrative_delta_records_non_material_repeat():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, future=True, expire_on_commit=False)

    with TestSession() as session:
        company = Company(name="RepeatCo", aliases=[], sector=None)
        session.add(company)
        session.flush()
        source = Source(company_id=company.id, url="https://example.com/1", status="fetched")
        session.add(source)
        session.flush()
        first_event = SignalEvent(
            company_id=company.id,
            source_id=source.id,
            signal_type="Other",
            summary="RepeatCo discussed enterprise expansion strategy",
            evidence_excerpt="Evidence",
            extracted_fields={"geography": [], "counterparties": [], "themes": ["enterprise expansion"]},
            confidence=0.7,
            strength=0.7,
            score=70.0,
            event_fingerprint="repeat1",
        )
        session.add(first_event)
        session.flush()
        evaluate_narrative_delta(session, first_event)

        next_source = Source(company_id=company.id, url="https://example.com/2", status="fetched")
        session.add(next_source)
        session.flush()
        repeated_event = SignalEvent(
            company_id=company.id,
            source_id=next_source.id,
            signal_type="Other",
            summary="RepeatCo reiterated enterprise expansion strategy",
            evidence_excerpt="Evidence",
            extracted_fields={"geography": [], "counterparties": [], "themes": ["enterprise expansion"]},
            confidence=0.65,
            strength=0.65,
            score=65.0,
            event_fingerprint="repeat2",
        )
        session.add(repeated_event)
        session.flush()
        delta = evaluate_narrative_delta(session, repeated_event)
        should_alert = delta.should_alert
        delta_type = delta.delta_type
        session.commit()

    assert should_alert is False
    assert delta_type == []
