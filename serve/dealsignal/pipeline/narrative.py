from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from dealsignal.models.company_narrative import CompanyNarrative
from dealsignal.models.narrative_delta import NarrativeDelta
from dealsignal.models.signal_event import SignalEvent

logger = logging.getLogger(__name__)

ALERT_THRESHOLD = 0.6
VERTICAL_KEYWORDS = {
    "financial services": {"payments", "fintech", "banking"},
    "healthcare": {"healthcare", "life sciences", "hospital"},
    "retail": {"retail", "commerce", "ecommerce"},
    "manufacturing": {"manufacturing", "industrial", "factory"},
    "public sector": {"government", "public sector", "federal"},
    "developers": {"developer", "engineering", "api"},
    "security": {"security", "cybersecurity", "fraud"},
}
STRATEGY_PATTERNS = (
    "go-to-market",
    "enterprise expansion",
    "partnership",
    "platform strategy",
    "product expansion",
    "AI workflow",
    "automation",
    "international expansion",
)


@dataclass
class NarrativeState:
    summary: str
    geographies: list[str]
    verticals: list[str]
    themes: list[str]
    counterparties: list[str]
    strategic_phrases: list[str]


def evaluate_narrative_delta(session: Session, event: SignalEvent) -> NarrativeDelta:
    snapshot = session.scalar(select(CompanyNarrative).where(CompanyNarrative.company_id == event.company_id))
    previous = _snapshot_to_state(snapshot)
    current_event_state = _state_from_event(event)
    merged = _merge_states(previous, current_event_state)
    delta_payload = _compute_delta(previous, merged)
    delta_types = [key for key, values in delta_payload.items() if values]
    significance = _score_delta(delta_payload, event)
    should_alert = significance >= ALERT_THRESHOLD and bool(delta_types)
    reason = _build_reason(delta_payload, significance, should_alert)

    if snapshot is None:
        snapshot = CompanyNarrative(company_id=event.company_id)
        session.add(snapshot)
        session.flush()

    snapshot.summary = merged.summary
    snapshot.geographies = merged.geographies
    snapshot.verticals = merged.verticals
    snapshot.themes = merged.themes
    snapshot.counterparties = merged.counterparties
    snapshot.strategic_phrases = merged.strategic_phrases
    snapshot.latest_event_id = event.id
    snapshot.updated_at = datetime.utcnow()

    delta = NarrativeDelta(
        company_id=event.company_id,
        source_event_id=event.id,
        delta_type=delta_types,
        delta_payload=delta_payload,
        significance_score=significance,
        should_alert=should_alert,
        reason=reason,
    )
    session.add(delta)
    session.flush()
    logger.info(
        "Narrative delta evaluated for event=%s | alert=%s | significance=%.2f | types=%s",
        event.id,
        should_alert,
        significance,
        ",".join(delta_types) or "none",
    )
    return delta


def _snapshot_to_state(snapshot: CompanyNarrative | None) -> NarrativeState:
    if snapshot is None:
        return NarrativeState("", [], [], [], [], [])
    return NarrativeState(
        summary=snapshot.summary or "",
        geographies=_sorted_unique(snapshot.geographies),
        verticals=_sorted_unique(snapshot.verticals),
        themes=_sorted_unique(snapshot.themes),
        counterparties=_sorted_unique(snapshot.counterparties),
        strategic_phrases=_sorted_unique(snapshot.strategic_phrases),
    )


def _state_from_event(event: SignalEvent) -> NarrativeState:
    fields = event.extracted_fields or {}
    summary = (event.summary or "").strip()
    themes = _sorted_unique(fields.get("themes", []))
    return NarrativeState(
        summary=summary,
        geographies=_sorted_unique(fields.get("geography", [])),
        verticals=_infer_verticals(summary, themes),
        themes=themes,
        counterparties=_sorted_unique(fields.get("counterparties", [])),
        strategic_phrases=_extract_strategic_phrases(summary, themes),
    )


def _merge_states(previous: NarrativeState, current: NarrativeState) -> NarrativeState:
    return NarrativeState(
        summary=current.summary or previous.summary,
        geographies=_sorted_unique(previous.geographies + current.geographies),
        verticals=_sorted_unique(previous.verticals + current.verticals),
        themes=_sorted_unique(previous.themes + current.themes),
        counterparties=_sorted_unique(previous.counterparties + current.counterparties),
        strategic_phrases=_sorted_unique(previous.strategic_phrases + current.strategic_phrases),
    )


def _compute_delta(previous: NarrativeState, current: NarrativeState) -> dict[str, list[str]]:
    return {
        "new_geographies": _new_values(previous.geographies, current.geographies),
        "new_verticals": _new_values(previous.verticals, current.verticals),
        "new_themes": _new_values(previous.themes, current.themes),
        "new_counterparties": _new_values(previous.counterparties, current.counterparties),
        "new_strategy_phrases": _new_values(previous.strategic_phrases, current.strategic_phrases),
    }


def _score_delta(delta_payload: dict[str, list[str]], event: SignalEvent) -> float:
    score = 0.0
    if delta_payload["new_geographies"]:
        score += 0.35
    if delta_payload["new_verticals"]:
        score += 0.35
    if delta_payload["new_counterparties"]:
        score += 0.25
    if delta_payload["new_themes"]:
        score += 0.2
    if delta_payload["new_strategy_phrases"]:
        score += 0.15
    if event.confidence >= 0.8:
        score += 0.1
    if event.strength >= 0.8:
        score += 0.1
    if len(delta_payload["new_geographies"]) + len(delta_payload["new_verticals"]) + len(delta_payload["new_counterparties"]) > 1:
        score += 0.1
    return round(min(score, 1.0), 2)


def _build_reason(delta_payload: dict[str, list[str]], significance: float, should_alert: bool) -> str:
    parts: list[str] = []
    if delta_payload["new_geographies"]:
        parts.append(f"new geography: {', '.join(delta_payload['new_geographies'])}")
    if delta_payload["new_verticals"]:
        parts.append(f"new vertical: {', '.join(delta_payload['new_verticals'])}")
    if delta_payload["new_counterparties"]:
        parts.append(f"new counterparty: {', '.join(delta_payload['new_counterparties'])}")
    if delta_payload["new_themes"]:
        parts.append(f"new theme: {', '.join(delta_payload['new_themes'])}")
    if delta_payload["new_strategy_phrases"]:
        parts.append(f"new strategy phrase: {', '.join(delta_payload['new_strategy_phrases'])}")
    if not parts:
        return f"No material change detected. Significance={significance:.2f}"
    prefix = "Alert-worthy change detected" if should_alert else "Change recorded"
    return f"{prefix}: " + "; ".join(parts) + f". Significance={significance:.2f}"


def _infer_verticals(summary: str, themes: list[str]) -> list[str]:
    text = f"{summary} {' '.join(themes)}".lower()
    matches: list[str] = []
    for vertical, keywords in VERTICAL_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            matches.append(vertical)
    return _sorted_unique(matches)


def _extract_strategic_phrases(summary: str, themes: list[str]) -> list[str]:
    text = f"{summary} {' '.join(themes)}".lower()
    phrases = [pattern for pattern in STRATEGY_PATTERNS if pattern in text]
    normalized = re.findall(r"\b(?:expand|launch|partner|invest|acquire|hire|enter)\w*\b", text)
    return _sorted_unique(phrases + normalized[:5])


def _new_values(previous: list[str], current: list[str]) -> list[str]:
    previous_set = {item.lower(): item for item in previous}
    return [item for item in current if item.lower() not in previous_set]


def _sorted_unique(values: list[str] | tuple[str, ...]) -> list[str]:
    seen: dict[str, str] = {}
    for value in values:
        item = str(value).strip()
        if not item:
            continue
        key = item.lower()
        if key not in seen:
            seen[key] = item
    return [seen[key] for key in sorted(seen)]
