from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from dealsignal.models.signal_event import SignalEvent


def generate_daily_digest(
    session: Session, output_path: str | Path = "reports/daily_digest.md", limit: int = 10
) -> str:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
    events = session.scalars(
        select(SignalEvent)
        .where(SignalEvent.created_at >= cutoff)
        .order_by(SignalEvent.score.desc())
        .limit(limit)
    ).all()

    lines = ["# DealSignal Daily Opportunity Digest", ""]
    lines.append(f"Generated at: {datetime.utcnow().isoformat()} UTC")
    lines.append("")

    if not events:
        lines.append("No signals found in the last 7 days.")
    else:
        for idx, event in enumerate(events, start=1):
            lines.extend(
                [
                    f"## {idx}. {event.company.name} - {event.signal_type}",
                    f"- Score: {event.score}",
                    f"- Summary: {event.summary}",
                    f"- Evidence: {event.evidence_excerpt}",
                    f"- Source URL: {event.source.url}",
                    "",
                ]
            )

    digest_text = "\n".join(lines)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(digest_text, encoding="utf-8")
    return digest_text

