from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dealsignal.models.database import Base


class LeadScore(Base):
    __tablename__ = "lead_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    source_event_id: Mapped[int] = mapped_column(ForeignKey("signal_events.id"), nullable=False, unique=True, index=True)
    narrative_delta_id: Mapped[int | None] = mapped_column(
        ForeignKey("narrative_deltas.id"),
        nullable=True,
        unique=True,
        index=True,
    )
    lead_score: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    change_significance_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    signal_strength_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    recency_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reinforcement_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    thesis_fit_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    relationship_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    source_quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    company = relationship("Company", back_populates="lead_scores")
    source_event = relationship("SignalEvent", back_populates="lead_score")
    narrative_delta = relationship("NarrativeDelta", back_populates="lead_score")
