from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dealsignal.models.database import Base


class NarrativeDelta(Base):
    __tablename__ = "narrative_deltas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    source_event_id: Mapped[int] = mapped_column(ForeignKey("signal_events.id"), nullable=False, index=True)
    delta_type: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    delta_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    significance_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    should_alert: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    company = relationship("Company", back_populates="narrative_deltas")
    source_event = relationship("SignalEvent", back_populates="narrative_delta")
