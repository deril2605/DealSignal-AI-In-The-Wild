from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from dealsignal.models.scoring_config import ScoringConfig


@dataclass(frozen=True)
class ScoringSettings:
    alert_threshold: float
    lead_change_weight: float
    lead_strength_weight: float
    lead_recency_weight: float
    lead_reinforcement_weight: float
    lead_thesis_weight: float
    lead_source_weight: float


DEFAULT_SETTINGS = ScoringSettings(
    alert_threshold=0.6,
    lead_change_weight=0.24,
    lead_strength_weight=0.24,
    lead_recency_weight=0.14,
    lead_reinforcement_weight=0.14,
    lead_thesis_weight=0.16,
    lead_source_weight=0.08,
)


def get_scoring_settings(session: Session) -> ScoringSettings:
    config = session.scalar(select(ScoringConfig).order_by(ScoringConfig.id.asc()).limit(1))
    if config is None:
        return DEFAULT_SETTINGS
    return ScoringSettings(
        alert_threshold=config.alert_threshold,
        lead_change_weight=config.lead_change_weight,
        lead_strength_weight=config.lead_strength_weight,
        lead_recency_weight=config.lead_recency_weight,
        lead_reinforcement_weight=config.lead_reinforcement_weight,
        lead_thesis_weight=config.lead_thesis_weight,
        lead_source_weight=config.lead_source_weight,
    )


def ensure_scoring_config(session: Session) -> ScoringConfig:
    config = session.scalar(select(ScoringConfig).order_by(ScoringConfig.id.asc()).limit(1))
    if config is None:
        config = ScoringConfig(
            alert_threshold=DEFAULT_SETTINGS.alert_threshold,
            lead_change_weight=DEFAULT_SETTINGS.lead_change_weight,
            lead_strength_weight=DEFAULT_SETTINGS.lead_strength_weight,
            lead_recency_weight=DEFAULT_SETTINGS.lead_recency_weight,
            lead_reinforcement_weight=DEFAULT_SETTINGS.lead_reinforcement_weight,
            lead_thesis_weight=DEFAULT_SETTINGS.lead_thesis_weight,
            lead_source_weight=DEFAULT_SETTINGS.lead_source_weight,
        )
        session.add(config)
        session.flush()
    return config


def update_scoring_config(session: Session, **values: float) -> ScoringConfig:
    config = ensure_scoring_config(session)
    for key, value in values.items():
        setattr(config, key, value)
    config.updated_at = datetime.utcnow()
    session.flush()
    return config
