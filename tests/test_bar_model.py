"""Section 14: Bar model tests — no IBKR connection needed."""

from datetime import datetime, timezone

from data.bar import Bar


def test_bar01_immutable():
    b = Bar(
        "AAPL", datetime(2024, 1, 1, tzinfo=timezone.utc), 150.0, 155.0, 149.0, 153.0, 1_000_000
    )
    try:
        b.close = 999.0
        assert False, "Should have raised FrozenInstanceError"
    except Exception:
        pass


def test_bar02_mid_and_range():
    b = Bar("MSFT", datetime(2024, 1, 1, tzinfo=timezone.utc), 400.0, 410.0, 390.0, 405.0, 500_000)
    assert b.mid == 400.0
    assert b.range == 20.0


def test_bar03_repr_readable():
    b = Bar("GE", datetime(2024, 1, 1, tzinfo=timezone.utc), 10.0, 11.0, 9.5, 10.5, 100_000)
    r = repr(b)
    assert "GE" in r and "10.00" in r
