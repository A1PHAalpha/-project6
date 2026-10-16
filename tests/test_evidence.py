"""证据过期判定的边界单测。"""
from datetime import datetime, timedelta, timezone

from rag_engine.evidence_builder import _is_stale


def _iso_minutes_ago(minutes: int) -> str:
    ts = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return ts.astimezone().isoformat(timespec="seconds")


def test_not_stale_at_29_minutes():
    assert _is_stale(_iso_minutes_ago(29)) is False


def test_stale_at_31_minutes():
    assert _is_stale(_iso_minutes_ago(31)) is True


def test_invalid_timestamp_not_stale():
    assert _is_stale("not-a-date") is False
