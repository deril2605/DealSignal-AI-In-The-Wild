from __future__ import annotations

from datetime import datetime, timezone


def recency_weight(published_at: datetime | None, now: datetime | None = None) -> float:
    if now is None:
        now = datetime.now(timezone.utc)
    if published_at is None:
        return 0.2
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    days_old = (now - published_at).days
    if days_old <= 2:
        return 1.0
    if days_old <= 7:
        return 0.7
    if days_old <= 30:
        return 0.4
    return 0.2


def compute_signal_score(confidence: float, strength: float, published_at: datetime | None) -> float:
    confidence = max(0.0, min(1.0, confidence))
    strength = max(0.0, min(1.0, strength))
    recency = recency_weight(published_at)
    score = 100 * ((0.45 * confidence) + (0.35 * strength) + (0.20 * recency))
    return round(score, 2)

