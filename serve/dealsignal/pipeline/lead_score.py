from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from dealsignal.models.lead_score import LeadScore
from dealsignal.models.narrative_delta import NarrativeDelta
from dealsignal.models.signal_event import SignalEvent
from dealsignal.models.source import Source
from dealsignal.pipeline.discover import WatchlistEntity, load_watchlist
from dealsignal.pipeline.score import recency_weight

logger = logging.getLogger(__name__)

SOURCE_QUALITY_DOMAINS = {
    "sec.gov": 95.0,
    "reuters.com": 92.0,
    "bloomberg.com": 92.0,
    "ft.com": 88.0,
    "wsj.com": 88.0,
    "theinformation.com": 84.0,
    "techcrunch.com": 78.0,
    "cnbc.com": 75.0,
    "prnewswire.com": 62.0,
    "businesswire.com": 62.0,
}
SIGNAL_TYPE_STRENGTH = {
    "M&A / Acquisition Intent": 100.0,
    "Fundraising / Capital Raise": 90.0,
    "Strategic Partnership": 82.0,
    "Geographic Expansion": 80.0,
    "Product Expansion": 76.0,
    "Hiring Surge": 68.0,
    "Leadership Change": 60.0,
    "Regulatory Signal": 72.0,
    "Other": 50.0,
}
INTENT_PATTERNS = (
    (re.compile(r"\bactively explor\w+ acquisitions?\b", re.I), 100.0),
    (re.compile(r"\b(acquisition|acquire|merger|buyout)\b", re.I), 95.0),
    (re.compile(r"\b(capital raise|fundraising|funding round|series [a-z])\b", re.I), 88.0),
    (re.compile(r"\b(expand|expansion|entering|launch\w+)\b", re.I), 76.0),
    (re.compile(r"\b(partner\w+|alliance|ecosystem)\b", re.I), 74.0),
    (re.compile(r"\b(ai|artificial intelligence)\b", re.I), 42.0),
)
THESIS_KEYWORDS = {
    "enterprise expansion": {"enterprise", "large customer", "go-to-market"},
    "strategic partnerships": {"partnership", "alliance", "ecosystem"},
    "enterprise partnerships": {"partnership", "alliance", "channel"},
    "ecosystem partnerships": {"ecosystem", "partner", "developer"},
    "product bundling strategy": {"bundle", "suite", "cross-sell"},
    "AI product strategy": {"ai", "copilot", "workflow"},
    "AI workflow expansion": {"ai", "workflow", "automation"},
    "enterprise automation": {"automation", "workflow", "operations"},
    "SMB expansion": {"smb", "mid-market", "small business"},
    "payments": {"payments", "checkout", "card", "merchant"},
}


@dataclass(frozen=True)
class ThesisProfile:
    themes: tuple[str, ...]
    sector: str | None


def evaluate_lead_score(session: Session, event: SignalEvent, delta: NarrativeDelta | None) -> LeadScore:
    thesis_profile = thesis_profile_for_company(event.company.name, event.company.sector)
    change_significance_score = round(((delta.significance_score if delta else 0.0) * 100), 2)
    signal_strength_score = _signal_strength_score(event)
    recency_score = round(recency_weight(event.source.published_at or event.source.discovered_at) * 100, 2)
    reinforcement_score = _reinforcement_score(session, event)
    thesis_fit_score = _thesis_fit_score(event, thesis_profile)
    relationship_score = 0.0
    source_quality_score = _source_quality_score(event.source.url)
    lead_score = _weighted_lead_score(
        change_significance_score=change_significance_score,
        signal_strength_score=signal_strength_score,
        recency_score=recency_score,
        reinforcement_score=reinforcement_score,
        thesis_fit_score=thesis_fit_score,
        source_quality_score=source_quality_score,
        relationship_score=relationship_score,
    )
    explanation = _build_explanation(
        lead_score=lead_score,
        thesis_fit_score=thesis_fit_score,
        signal_strength_score=signal_strength_score,
        recency_score=recency_score,
        reinforcement_score=reinforcement_score,
        source_quality_score=source_quality_score,
        relationship_score=relationship_score,
    )

    score = LeadScore(
        company_id=event.company_id,
        source_event_id=event.id,
        narrative_delta_id=delta.id if delta else None,
        lead_score=lead_score,
        change_significance_score=change_significance_score,
        signal_strength_score=signal_strength_score,
        recency_score=recency_score,
        reinforcement_score=reinforcement_score,
        thesis_fit_score=thesis_fit_score,
        relationship_score=relationship_score,
        source_quality_score=source_quality_score,
        explanation=explanation,
    )
    session.add(score)
    session.flush()
    logger.info(
        "Lead score evaluated for event=%s | lead=%.2f | thesis=%.2f | reinforcement=%.2f",
        event.id,
        lead_score,
        thesis_fit_score,
        reinforcement_score,
    )
    return score


@lru_cache(maxsize=1)
def _watchlist_lookup() -> dict[str, WatchlistEntity]:
    return {entity.name.lower(): entity for entity in load_watchlist()}


def thesis_profile_for_company(company_name: str, sector: str | None) -> ThesisProfile:
    entity = _watchlist_lookup().get(company_name.lower())
    themes = tuple(entity.themes) if entity else ()
    resolved_sector = entity.sector if entity and entity.sector else sector
    return ThesisProfile(themes=themes, sector=resolved_sector)


def _signal_strength_score(event: SignalEvent) -> float:
    baseline = 100 * ((0.55 * event.strength) + (0.25 * event.confidence))
    text = f"{event.signal_type} {event.summary} {' '.join((event.extracted_fields or {}).get('themes', []))}"
    intent_score = max((score for pattern, score in INTENT_PATTERNS if pattern.search(text)), default=45.0)
    signal_type_score = SIGNAL_TYPE_STRENGTH.get(event.signal_type, 55.0)
    return round(min(100.0, (0.4 * baseline) + (0.35 * intent_score) + (0.25 * signal_type_score)), 2)


def _reinforcement_score(session: Session, event: SignalEvent) -> float:
    now = datetime.utcnow()
    floor = now - timedelta(days=21)
    prior_events = session.scalars(
        select(SignalEvent)
        .join(Source, SignalEvent.source_id == Source.id)
        .where(SignalEvent.company_id == event.company_id, SignalEvent.id != event.id, SignalEvent.created_at >= floor)
    ).all()
    corroborating_domains = {
        _domain(candidate.source.url)
        for candidate in prior_events
        if _is_related_event(event, candidate) and _domain(candidate.source.url)
    }
    corroboration_count = len(corroborating_domains)
    if corroboration_count == 0:
        return 28.0
    if corroboration_count == 1:
        return 56.0
    if corroboration_count == 2:
        return 78.0
    return 92.0


def _thesis_fit_score(event: SignalEvent, thesis_profile: ThesisProfile) -> float:
    if not thesis_profile.themes and not thesis_profile.sector:
        return 35.0
    event_terms = _normalized_terms(
        [event.signal_type, event.summary, *((event.extracted_fields or {}).get("themes", []))]
    )
    matches = 0
    total = 0
    for theme in thesis_profile.themes:
        total += 1
        theme_terms = _normalized_terms([theme])
        keyword_terms = _normalized_terms(THESIS_KEYWORDS.get(theme.lower(), set()))
        if event_terms.intersection(theme_terms | keyword_terms):
            matches += 1
    if thesis_profile.sector:
        total += 1
        if event_terms.intersection(_normalized_terms([thesis_profile.sector])):
            matches += 1
    if total == 0:
        return 35.0
    ratio = matches / total
    if ratio == 0:
        return 22.0
    return round(min(100.0, 35.0 + (ratio * 65.0)), 2)


def _source_quality_score(url: str) -> float:
    domain = _domain(url)
    for known_domain, score in SOURCE_QUALITY_DOMAINS.items():
        if domain.endswith(known_domain):
            return score
    if domain.endswith(".gov"):
        return 88.0
    if domain:
        return 58.0
    return 40.0


def _weighted_lead_score(
    *,
    change_significance_score: float,
    signal_strength_score: float,
    recency_score: float,
    reinforcement_score: float,
    thesis_fit_score: float,
    source_quality_score: float,
    relationship_score: float,
) -> float:
    weighted = (
        (0.24 * change_significance_score)
        + (0.24 * signal_strength_score)
        + (0.14 * recency_score)
        + (0.14 * reinforcement_score)
        + (0.16 * thesis_fit_score)
        + (0.08 * source_quality_score)
    )
    if relationship_score > 0:
        weighted += min(8.0, relationship_score * 0.08)
    return round(min(100.0, weighted), 2)


def _build_explanation(
    *,
    lead_score: float,
    thesis_fit_score: float,
    signal_strength_score: float,
    recency_score: float,
    reinforcement_score: float,
    source_quality_score: float,
    relationship_score: float,
) -> str:
    factors: list[str] = []
    if signal_strength_score >= 80:
        factors.append("strong strategic intent")
    if thesis_fit_score >= 75:
        factors.append("high thesis fit")
    if recency_score >= 85:
        factors.append("very recent signal")
    if reinforcement_score >= 75:
        factors.append("multi-source reinforcement")
    if source_quality_score >= 85:
        factors.append("high-trust source")
    if relationship_score > 0:
        factors.append("warm relationship bonus")
    if not factors:
        factors.append("baseline event quality")
    return f"Lead score {lead_score:.2f} driven by " + ", ".join(factors) + "."


def _is_related_event(left: SignalEvent, right: SignalEvent) -> bool:
    if left.signal_type == right.signal_type:
        return True
    left_fields = left.extracted_fields or {}
    right_fields = right.extracted_fields or {}
    left_terms = {
        *_normalized_terms(left_fields.get("themes", [])),
        *_normalized_terms(left_fields.get("counterparties", [])),
    }
    right_terms = {
        *_normalized_terms(right_fields.get("themes", [])),
        *_normalized_terms(right_fields.get("counterparties", [])),
    }
    return bool(left_terms.intersection(right_terms))


def _normalized_terms(values: list[str] | tuple[str, ...] | set[str]) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        raw = str(value).strip().lower()
        if not raw:
            continue
        tokens.add(raw)
        for part in re.split(r"[^a-z0-9]+", raw):
            if len(part) >= 3:
                tokens.add(part)
    return tokens


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().replace("www.", "")
