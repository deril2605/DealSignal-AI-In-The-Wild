from datetime import datetime, timedelta

from dealsignal.app.routes import _change_status_for_feed, _delta_to_view, _event_card_view, _priority_now_cards
from dealsignal.models.company import Company
from dealsignal.models.lead_score import LeadScore
from dealsignal.models.narrative_delta import NarrativeDelta
from dealsignal.models.signal_event import SignalEvent
from dealsignal.models.source import Source


def _build_event(*, company_id: int, company_name: str, event_id: int, days_ago: int, lead_score: float) -> SignalEvent:
    company = Company(id=company_id, name=company_name, aliases=[], sector=None)
    source = Source(
        id=event_id,
        company_id=company_id,
        company=company,
        url=f"https://example.com/{event_id}",
        status="fetched",
        discovered_at=datetime.utcnow() - timedelta(days=days_ago),
    )
    event = SignalEvent(
        id=event_id,
        company_id=company_id,
        company=company,
        source_id=source.id,
        source=source,
        signal_type="Strategic Partnership",
        summary=f"{company_name} signed a partner",
        evidence_excerpt="Evidence",
        extracted_fields={"geography": ["US"], "counterparties": ["Partner"], "themes": ["partner ecosystem"]},
        confidence=0.9,
        strength=0.9,
        score=90.0,
        event_fingerprint=f"fp-{event_id}",
        created_at=datetime.utcnow() - timedelta(days=days_ago),
    )
    delta = NarrativeDelta(
        company_id=company_id,
        source_event_id=event_id,
        source_event=event,
        delta_type=["new_counterparties"],
        delta_payload={"new_counterparties": ["Partner"]},
        significance_score=0.8,
        should_alert=True,
        reason="Alert-worthy change detected",
    )
    score = LeadScore(
        company_id=company_id,
        source_event_id=event_id,
        source_event=event,
        lead_score=lead_score,
        change_significance_score=80.0,
        signal_strength_score=90.0,
        recency_score=90.0,
        reinforcement_score=60.0,
        thesis_fit_score=75.0,
        relationship_score=0.0,
        source_quality_score=80.0,
        explanation="High-conviction recent signal.",
    )
    event.narrative_delta = delta
    event.lead_score = score
    return event


def test_stale_or_low_scoring_alerts_are_downgraded_in_feed():
    now = datetime.utcnow()
    fresh_event = _build_event(company_id=1, company_name="FreshCo", event_id=1, days_ago=2, lead_score=86.0)
    stale_event = _build_event(company_id=2, company_name="StaleCo", event_id=2, days_ago=12, lead_score=91.0)
    weak_event = _build_event(company_id=3, company_name="WeakCo", event_id=3, days_ago=1, lead_score=68.0)

    assert _change_status_for_feed(fresh_event, _delta_to_view(fresh_event.narrative_delta), {"lead_score": 86.0}, now) == "alert"
    assert _change_status_for_feed(stale_event, _delta_to_view(stale_event.narrative_delta), {"lead_score": 91.0}, now) == "recorded"
    assert _change_status_for_feed(weak_event, _delta_to_view(weak_event.narrative_delta), {"lead_score": 68.0}, now) == "recorded"


def test_priority_now_is_capped_to_two_events_per_company():
    now = datetime.utcnow()
    alpha_best = _event_card_view(_build_event(company_id=1, company_name="Alpha", event_id=1, days_ago=1, lead_score=92.0), now=now)
    alpha_second = _event_card_view(_build_event(company_id=1, company_name="Alpha", event_id=2, days_ago=0, lead_score=88.0), now=now)
    beta_best = _event_card_view(_build_event(company_id=2, company_name="Beta", event_id=3, days_ago=1, lead_score=87.0), now=now)
    alpha_third = _event_card_view(_build_event(company_id=1, company_name="Alpha", event_id=4, days_ago=0, lead_score=84.0), now=now)

    cards = sorted(
        [alpha_best, alpha_second, beta_best, alpha_third],
        key=lambda card: (-card["lead_score_value"], -card["effective_at_sort_ts"]),
    )
    curated = _priority_now_cards(cards)

    assert len(curated) == 3
    assert {card["event"].company.name for card in curated} == {"Alpha", "Beta"}
    assert {card["event"].id for card in curated} == {1, 2, 3}
