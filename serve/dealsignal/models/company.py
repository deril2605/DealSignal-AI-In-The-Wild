from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dealsignal.models.database import Base


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    aliases: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    sector: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    sources = relationship("Source", back_populates="company", cascade="all, delete-orphan")
    signal_events = relationship("SignalEvent", back_populates="company", cascade="all, delete-orphan")

