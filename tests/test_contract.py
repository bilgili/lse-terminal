import pytest

from lse_terminal.contracts import TIMEFRAME_SECONDS, to_epoch


def test_timeframe_table_has_no_tick():
    assert "tick" not in TIMEFRAME_SECONDS
    assert TIMEFRAME_SECONDS["1m"] == 60
    assert TIMEFRAME_SECONDS["1d"] == 86400
    assert list(TIMEFRAME_SECONDS) == [
        "1s", "30s", "1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]


@pytest.mark.parametrize("value,expected", [
    (1700000000, 1700000000),
    (1700000000.9, 1700000000),
    ("1700000000", 1700000000),
    ("2024-01-02", 1704153600),
    ("2024-01-02T03:04:05Z", 1704164645),
    ("2024-01-02T03:04:05+00:00", 1704164645),
    ("2024-01-02T05:04:05+02:00", 1704164645),
    ("2024-01-02 03:04:05", 1704164645),  # naive means UTC
    ("-5", -5),
    ("  1700000000 ", 1700000000),
])
def test_to_epoch_shapes(value, expected):
    assert to_epoch(value) == expected


def test_to_epoch_none_and_empty():
    assert to_epoch(None) is None
    assert to_epoch("") is None


def test_to_epoch_whitespace_only_is_empty():
    assert to_epoch("  ") is None


def test_to_epoch_rejects_bool():
    with pytest.raises(TypeError):
        to_epoch(True)


@pytest.mark.parametrize("bad", ["nope", "1700000000.5"])
def test_to_epoch_names_bad_input(bad):
    with pytest.raises(ValueError, match="unparseable bound"):
        to_epoch(bad)
