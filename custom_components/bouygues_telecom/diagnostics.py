"""Diagnostics support for Bouygues Telecom."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_COOKIES, CONF_PERSON_ID
from .coordinator import BouyguesTelecomConfigEntry

TO_REDACT = {
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_COOKIES,
    CONF_PERSON_ID,
    "numeroTel",
    "nom",
    "idPfs",
    "idFacture",
    "href",
    "iccid",
    "imei",
    "id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BouyguesTelecomConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = entry.runtime_data.data
    return async_redact_data(
        {
            "entry": {"data": dict(entry.data), "options": dict(entry.options)},
            "lines": [asdict(line) for line in data.lines.values()],
            "invoices": data.invoices[:3],
        },
        TO_REDACT,
    )
