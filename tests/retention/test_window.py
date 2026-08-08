"""raw log 保留時段 —— 純函數（票 11）。"""

from datetime import datetime, timedelta, timezone

from purple.retention.window import (
    POST_WINDOW_MINUTES,
    PRE_WINDOW_MINUTES,
    RetentionWindow,
)

import pytest

START = datetime(2026, 8, 8, 14, 0, 0, tzinfo=timezone.utc)
END = datetime(2026, 8, 8, 15, 0, 0, tzinfo=timezone.utc)
WIN = RetentionWindow(START, END)


class TestWindowBounds:
    def test_opens_ten_minutes_before_start(self):
        assert WIN.opens_at == START - timedelta(minutes=PRE_WINDOW_MINUTES)

    def test_closes_thirty_minutes_after_end(self):
        assert WIN.closes_at == END + timedelta(minutes=POST_WINDOW_MINUTES)

    def test_offsets_are_named_constants(self):
        assert PRE_WINDOW_MINUTES == 10
        assert POST_WINDOW_MINUTES == 30


class TestRawAvailability:
    def test_raw_available_inside_window(self):
        assert WIN.retains(datetime(2026, 8, 8, 14, 30, tzinfo=timezone.utc))

    def test_raw_available_in_the_pre_window(self):
        assert WIN.retains(START - timedelta(minutes=5))

    def test_raw_absent_outside_window(self):
        assert not WIN.retains(START - timedelta(minutes=30))
        assert not WIN.retains(END + timedelta(minutes=45))

    def test_boundaries_are_inclusive(self):
        assert WIN.retains(WIN.opens_at)
        assert WIN.retains(WIN.closes_at)


class TestIsOpen:
    def test_open_during_exercise(self):
        assert WIN.is_open(datetime(2026, 8, 8, 14, 30, tzinfo=timezone.utc))

    def test_closed_long_after(self):
        assert not WIN.is_open(END + timedelta(hours=2))


class TestTimezoneAndOrder:
    def test_naive_timestamp_is_rejected(self):
        with pytest.raises(ValueError, match="無時區"):
            WIN.retains(datetime(2026, 8, 8, 14, 30))

    def test_end_before_start_is_rejected(self):
        with pytest.raises(ValueError, match="早於"):
            RetentionWindow(END, START)
