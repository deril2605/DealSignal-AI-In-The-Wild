from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from dealsignal.models.company import Company
from dealsignal.models.database import Base
from dealsignal.models.signal_event import SignalEvent
from dealsignal.models.source import Source
from dealsignal.pipeline.extract import is_duplicate_event


def test_is_duplicate_event_detects_existing_fingerprint():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, future=True)

    with TestSession() as session:
        company = Company(name="TestCo", aliases=[], sector=None)
        session.add(company)
        session.flush()
        source = Source(company_id=company.id, url="https://example.com", status="fetched")
        session.add(source)
        session.flush()
        session.add(
            SignalEvent(
                company_id=company.id,
                source_id=source.id,
                signal_type="Other",
                summary="Signal summary",
                evidence_excerpt="Evidence",
                extracted_fields={},
                confidence=0.5,
                strength=0.5,
                score=50.0,
                event_fingerprint="abc123",
            )
        )
        session.commit()

    with Session(engine) as session:
        assert is_duplicate_event(session, "abc123") is True
        assert is_duplicate_event(session, "different") is False

