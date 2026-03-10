from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dealsignal.models.database import Base


class CompanyNarrative(Base):
    __tablename__ = "company_narratives"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, unique=True, index=True)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    geographies: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    verticals: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    themes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    counterparties: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    strategic_phrases: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    latest_event_id: Mapped[int | None] = mapped_column(ForeignKey("signal_events.id"), nullable=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    company = relationship("Company", back_populates="narrative")
