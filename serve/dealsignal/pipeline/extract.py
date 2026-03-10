from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from dealsignal.models.signal_event import SignalEvent
from dealsignal.pipeline.lead_score import evaluate_lead_score
from dealsignal.pipeline.narrative import evaluate_narrative_delta
from dealsignal.models.source import Source
from dealsignal.pipeline.score import compute_signal_score

logger = logging.getLogger(__name__)

SIGNAL_TYPES = {
    "Geographic Expansion",
    "Fundraising / Capital Raise",
    "M&A / Acquisition Intent",
    "Strategic Partnership",
    "Product Expansion",
    "Hiring Surge",
    "Leadership Change",
    "Regulatory Signal",
    "Other",
}

STRATEGIC_TERMS = (
    "expansion",
    "fundraising",
    "funding",
    "round",
    "acquisition",
    "acquire",
    "partnership",
    "partner",
    "hiring",
    "leadership",
    "regulatory",
)


class ExtractedFields(BaseModel):
    geography: list[str] = Field(default_factory=list)
    timeline: str = ""
    counterparties: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    magnitude: str = ""


class SignalCandidate(BaseModel):
    signal_type: str
    summary: str
    evidence_excerpt: str
    extracted_fields: ExtractedFields = Field(default_factory=ExtractedFields)
    confidence: float
    strength: float


def generate_event_fingerprint(
    company_name: str,
    signal_type: str,
    extracted_fields: dict[str, Any],
    summary: str,
) -> str:
    geography = "|".join(sorted(extracted_fields.get("geography", [])))
    counterparties = "|".join(sorted(extracted_fields.get("counterparties", [])))
    themes = "|".join(sorted(extracted_fields.get("themes", [])))
    keyphrases = " ".join((summary or "").lower().split()[:12])
    raw = f"{company_name.lower()}::{signal_type.lower()}::{geography}::{counterparties}::{themes}::{keyphrases}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_duplicate_event(session: Session, fingerprint: str) -> bool:
    existing = session.scalar(
        select(SignalEvent.id).where(SignalEvent.event_fingerprint == fingerprint).limit(1)
    )
    return existing is not None


def extract_from_fetched_sources(session: Session) -> int:
    client = _build_azure_client()
    if client is None:
        logger.warning("Azure OpenAI client is not configured. Skipping extraction.")
        return 0

    min_article_chars = _env_int("MIN_ARTICLE_CHARS", 700)
    min_confidence = _env_float("MIN_SIGNAL_CONFIDENCE", 0.55)
    min_strength = _env_float("MIN_SIGNAL_STRENGTH", 0.50)
    min_evidence_chars = _env_int("MIN_EVIDENCE_CHARS", 60)

    fetched_sources = session.scalars(
        select(Source).where(Source.status.in_(["fetched", "extract_error"]))
    ).all()
    created = 0
    for source in fetched_sources:
        if not source.raw_text_path:
            source.status = "extract_error"
            continue
        text_path = Path(source.raw_text_path)
        if not text_path.exists():
            source.status = "extract_error"
            continue

        article_text = text_path.read_text(encoding="utf-8")
        if not _looks_signal_bearing(article_text, source.company.name, min_article_chars):
            source.status = "extracted"
            continue

        signals = extract_signals_with_llm(client, article_text)
        if not signals:
            source.status = "extracted"
            continue

        for signal in signals:
            if signal.confidence < min_confidence or signal.strength < min_strength:
                continue
            if len((signal.evidence_excerpt or "").strip()) < min_evidence_chars:
                continue
            fields = signal.extracted_fields.model_dump()
            fingerprint = generate_event_fingerprint(
                company_name=source.company.name,
                signal_type=signal.signal_type,
                extracted_fields=fields,
                summary=signal.summary,
            )
            if is_duplicate_event(session, fingerprint):
                continue
            score = compute_signal_score(
                confidence=signal.confidence,
                strength=signal.strength,
                published_at=source.published_at or source.discovered_at,
            )
            event = SignalEvent(
                company_id=source.company_id,
                source_id=source.id,
                signal_type=signal.signal_type,
                summary=signal.summary,
                evidence_excerpt=signal.evidence_excerpt,
                extracted_fields=fields,
                confidence=max(0.0, min(1.0, signal.confidence)),
                strength=max(0.0, min(1.0, signal.strength)),
                score=score,
                event_fingerprint=fingerprint,
            )
            session.add(event)
            session.flush()
            delta = evaluate_narrative_delta(session, event)
            evaluate_lead_score(session, event, delta)
            created += 1
        source.status = "extracted"

    logger.info("Extraction complete. New events: %s", created)
    return created


def extract_signals_with_llm(client: Any, article_text: str) -> list[SignalCandidate]:
    prompt = (
        "From this article extract strategic signals that may indicate business strategy, "
        "expansion, partnerships, acquisitions, fundraising, or investment activity. "
        "Only return signals supported by this text. Return strict JSON array with objects:\n"
        "{signal_type, summary, evidence_excerpt, extracted_fields:{geography,timeline,counterparties,themes,magnitude}, confidence, strength}\n"
        "confidence and strength must be numeric floats between 0 and 1.\n"
        "geography, counterparties, themes must be arrays of strings (use [] if unknown).\n"
        "Valid signal_type values: Geographic Expansion, Fundraising / Capital Raise, "
        "M&A / Acquisition Intent, Strategic Partnership, Product Expansion, Hiring Surge, "
        "Leadership Change, Regulatory Signal, Other."
    )
    user_text = article_text[:10000]
    try:
        response = client.chat.completions.create(
            model=os.getenv("LLM_MODEL", ""),
            messages=[
                {"role": "system", "content": "You are a precise information extraction engine."},
                {"role": "user", "content": f"{prompt}\n\nArticle:\n{user_text}"},
            ],
            temperature=0.0,
        )
        content = response.choices[0].message.content or "[]"
        payload = _parse_json_array(content)
        candidates: list[SignalCandidate] = []
        for item in payload:
            normalized = _normalize_signal_item(item)
            try:
                candidate = SignalCandidate.model_validate(normalized)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipping invalid signal item: %s", exc)
                continue
            if candidate.signal_type not in SIGNAL_TYPES:
                candidate.signal_type = "Other"
            candidates.append(candidate)
        return candidates
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM extraction failed: %s", exc)
        return []


def _build_azure_client() -> Any | None:
    from openai import AzureOpenAI

    api_key = os.getenv("LLM_API_KEY")
    base_url = os.getenv("LLM_BASE_URL")
    model = os.getenv("LLM_MODEL")
    if not api_key or not base_url or not model:
        return None
    return AzureOpenAI(
        api_key=api_key,
        azure_endpoint=base_url.rstrip("/"),
        api_version=os.getenv("LLM_API_VERSION", "2024-02-15-preview"),
    )


def _parse_json_array(content: str) -> list[dict[str, Any]]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    cleaned = cleaned.strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def _normalize_signal_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}

    fields = item.get("extracted_fields")
    if not isinstance(fields, dict):
        fields = {}

    normalized = {
        "signal_type": str(item.get("signal_type") or "Other").strip() or "Other",
        "summary": str(item.get("summary") or "").strip(),
        "evidence_excerpt": str(item.get("evidence_excerpt") or "").strip(),
        "extracted_fields": {
            "geography": _ensure_list_of_strings(fields.get("geography")),
            "timeline": str(fields.get("timeline") or "").strip(),
            "counterparties": _ensure_list_of_strings(fields.get("counterparties")),
            "themes": _ensure_list_of_strings(fields.get("themes")),
            "magnitude": str(fields.get("magnitude") or "").strip(),
        },
        "confidence": _coerce_score(item.get("confidence")),
        "strength": _coerce_score(item.get("strength")),
    }
    return normalized


def _ensure_list_of_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        trimmed = value.strip()
        return [trimmed] if trimmed else []
    return [str(value).strip()] if str(value).strip() else []


def _coerce_score(value: Any) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    if isinstance(value, str):
        cleaned = value.strip().lower()
        label_map = {
            "very high": 0.95,
            "high": 0.85,
            "medium": 0.60,
            "moderate": 0.60,
            "low": 0.35,
            "very low": 0.15,
            "strong": 0.85,
            "weak": 0.35,
        }
        if cleaned in label_map:
            return label_map[cleaned]
        try:
            return max(0.0, min(1.0, float(cleaned)))
        except ValueError:
            return 0.5
    return 0.5


def _looks_signal_bearing(article_text: str, company_name: str, min_chars: int) -> bool:
    if len(article_text) < min_chars:
        return False
    lowered = article_text.lower()
    if company_name.lower() not in lowered:
        return False
    return any(term in lowered for term in STRATEGIC_TERMS)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default
