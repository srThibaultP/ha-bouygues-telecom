"""Accessors for the Bouygues Telecom API payloads."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from homeassistant.util import dt as dt_util

from .coordinator import LineData

PARIS = "Europe/Paris"
_COUNTER_KEYS = {"used": "quantiteConsommee", "total": "quantiteTotale"}


def data_counter(line: LineData, counter_type: str) -> dict[str, Any] | None:
    """Data counter of a type (PRINCIPALE, ADDITIONNELLE)."""
    return next(
        (
            c
            for c in line.consumption.get("compteursConsoDATA", [])
            if c.get("type") == counter_type
        ),
        None,
    )


def voice_counter(line: LineData) -> dict[str, Any] | None:
    """Main voice counter."""
    return next(
        (
            c
            for c in line.consumption.get("compteursConsoVOIX", [])
            if c.get("type") == "PRINCIPALE"
        ),
        None,
    )


def counter_value(counter: dict[str, Any] | None, which: str) -> float | None:
    """Used or total quantity; None when missing or unlimited (-1)."""
    if counter is None:
        return None
    value = counter.get(_COUNTER_KEYS[which])
    if value is None or value < 0:
        return None
    return float(value)


def counter_remaining(counter: dict[str, Any] | None) -> float | None:
    """Remaining quantity of a counter."""
    used, total = counter_value(counter, "used"), counter_value(counter, "total")
    if used is None or total is None:
        return None
    return max(total - used, 0.0)


def counter_percent(counter: dict[str, Any] | None) -> float | None:
    """Used share of a counter, in percent."""
    used, total = counter_value(counter, "used"), counter_value(counter, "total")
    if used is None or not total:
        return None
    return round(used / total * 100, 1)


def parse_date(value: str | None) -> date | None:
    """Parse a YYYY-MM-DD date."""
    return dt_util.parse_date(value) if value else None


def invoice_date(value: str | None) -> date | None:
    """Invoice dates are midnight Paris time sent as UTC (e.g. 23:00Z)."""
    if not value:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    return parsed.astimezone(dt_util.get_time_zone(PARIS)).date()


def paris_datetime(value: str | None) -> datetime | None:
    """Parse a timestamp, assuming Paris time when it has no offset."""
    if not value:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.get_time_zone(PARIS))
    return parsed


def period_start(line: LineData) -> datetime | None:
    """Start of the current billing period."""
    start = parse_date(line.consumption.get("dateDebutConso"))
    if start is None:
        return None
    return datetime.combine(start, datetime.min.time(), dt_util.get_time_zone(PARIS))


def main_offer(line: LineData) -> dict[str, Any]:
    """Plan of the line with its price."""
    offer: dict[str, Any] = line.details.get("plan", {}).get("offrePrincipale", {})
    return offer


def subscribed_options(line: LineData) -> list[str]:
    """Names of the subscribed options."""
    return [
        o["libelle"]
        for o in line.details.get("options", {}).get("items", [])
        if o.get("statut") == "SOUSCRITE" and o.get("libelle")
    ]


def renewal(line: LineData) -> dict[str, Any] | None:
    """Phone renewal eligibility of the line."""
    return next(
        (
            i
            for i in line.details.get("renewal", {}).get("items", [])
            if i.get("type") == "renouvellement"
        ),
        None,
    )


def strip_links(items: list[Any]) -> list[Any]:
    """Drop HAL _links from API objects before exposing them as attributes."""
    return [
        {k: v for k, v in i.items() if k != "_links"} if isinstance(i, dict) else i
        for i in items
    ]
