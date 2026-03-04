from datetime import datetime, timedelta, timezone

from dealsignal.pipeline.score import compute_signal_score, recency_weight


def test_recency_weight_buckets():
    now = datetime.now(timezone.utc)
    assert recency_weight(now - timedelta(days=1), now=now) == 1.0
    assert recency_weight(now - timedelta(days=5), now=now) == 0.7
    assert recency_weight(now - timedelta(days=20), now=now) == 0.4
    assert recency_weight(now - timedelta(days=45), now=now) == 0.2


def test_compute_signal_score_range():
    score = compute_signal_score(0.8, 0.6, datetime.now(timezone.utc))
    assert score > 0
    assert score <= 100

