from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dealsignal.models.database import Base


class OpportunityEval(Base):
    __tablename__ = "opportunity_evals"
    __table_args__ = (UniqueConstraint("snapshot_date", "signal_event_id", name="uq_eval_snapshot_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    signal_event_id: Mapped[int] = mapped_column(ForeignKey("signal_events.id"), nullable=False, index=True)
    lead_score_id: Mapped[int | None] = mapped_column(ForeignKey("lead_scores.id"), nullable=True, index=True)
    lead_score_value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    review_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    review_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    company = relationship("Company")
    signal_event = relationship("SignalEvent")
    lead_score = relationship("LeadScore")
