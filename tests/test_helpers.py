"""Edge cases of the payload accessors."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.bouygues_telecom import helpers as h
from custom_components.bouygues_telecom.coordinator import LineData


def _line(consumption: dict[str, Any]) -> LineData:
    return LineData(contract={}, consumption=consumption)


def test_missing_counters() -> None:
    """Lines without counters report nothing."""
    line = _line({})
    assert h.data_counter(line, "PRINCIPALE") is None
    assert h.voice_counter(line) is None
    assert h.counter_value(None, "used") is None
    assert h.counter_remaining(None) is None
    assert h.counter_percent(None) is None
    assert h.period_start(line) is None
    assert h.main_offer(line) == {}
    assert h.subscribed_options(line) == []
    assert h.renewal(line) is None


@pytest.mark.parametrize(
    "counter",
    [
        {"quantiteConsommee": 5, "quantiteTotale": -1},
        {"quantiteConsommee": None, "quantiteTotale": 10},
    ],
)
def test_unlimited_or_partial_counter(counter: dict[str, Any]) -> None:
    """Unlimited (-1) and missing quantities give no remaining or percentage."""
    assert h.counter_remaining(counter) is None
    assert h.counter_percent(counter) is None


def test_zero_allowance() -> None:
    """A zero allowance has no percentage."""
    assert h.counter_percent({"quantiteConsommee": 0, "quantiteTotale": 0}) is None


@pytest.mark.parametrize("value", [None, "", "not a date"])
def test_bad_dates(value: str | None) -> None:
    """Missing or invalid dates are unknown."""
    assert h.invoice_date(value) is None
    assert h.paris_datetime(value) is None


def test_dates() -> None:
    """Invoice dates are converted to Paris; offsets are kept."""
    assert str(h.invoice_date("2026-11-15T23:00:00.000Z")) == "2026-11-16"
    assert (
        h.paris_datetime("2026-11-26T23:48:19+00:00").utcoffset().total_seconds() == 0
    )
    assert h.parse_date(None) is None


def test_strip_links() -> None:
    """HAL links are removed, other values are kept."""
    assert h.strip_links([{"a": 1, "_links": {}}, "x"]) == [{"a": 1}, "x"]
