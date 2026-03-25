from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer
from sqlalchemy.orm import Mapped, mapped_column

from dealsignal.models.database import Base


class ScoringConfig(Base):
    __tablename__ = "scoring_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    alert_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.6)
    lead_change_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.24)
    lead_strength_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.24)
    lead_recency_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.14)
    lead_reinforcement_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.14)
    lead_thesis_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.16)
    lead_source_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.08)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
