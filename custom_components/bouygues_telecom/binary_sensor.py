"""Binary sensors of the Bouygues Telecom integration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import helpers as h
from .coordinator import (
    BouyguesTelecomConfigEntry,
    BouyguesTelecomCoordinator,
    LineData,
)
from .entity import BouyguesLineEntity

PARALLEL_UPDATES = 0

_THRESHOLDS = {
    "france_europe": "seuilDataHorsForfaitAtteintFREUR",
    "rest_of_world": "seuilDataHorsForfaitAtteintRDM",
    "any_zone": "seuilDataHorsForfaitAtteint",
}


@dataclass(frozen=True, kw_only=True)
class LineBinarySensorDescription(BinarySensorEntityDescription):
    """Description of a binary sensor of a mobile line."""

    is_on_fn: Callable[[LineData], bool | None]
    exists_fn: Callable[[LineData], bool] = lambda _: True
    attributes_fn: Callable[[LineData], Mapping[str, Any]] | None = None


BINARY_SENSORS: tuple[LineBinarySensorDescription, ...] = (
    LineBinarySensorDescription(
        key="network_incident",
        translation_key="network_incident",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda d: bool(d.extras["incidents"].get("incidentsCollectifs")),
        attributes_fn=lambda d: {
            "incidents": h.strip_links(
                d.extras["incidents"].get("incidentsCollectifs", [])
            )
        },
    ),
    LineBinarySensorDescription(
        key="data_threshold_reached",
        translation_key="data_threshold_reached",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda d: any(
            bool(d.consumption.get(k)) for k in _THRESHOLDS.values()
        ),
        attributes_fn=lambda d: {
            zone: bool(d.consumption.get(key)) for zone, key in _THRESHOLDS.items()
        },
    ),
    LineBinarySensorDescription(
        key="phone_renewal_eligible",
        translation_key="phone_renewal_eligible",
        is_on_fn=lambda d: (h.renewal(d) or {}).get("statut"),
        exists_fn=lambda d: h.renewal(d) is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BouyguesTelecomConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors, adding lines that appear on the account later on."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_lines() -> None:
        current = set(coordinator.data.lines)
        # Forget removed lines so they get entities again if they come back.
        known.intersection_update(current)
        new = current - known
        known.update(new)
        async_add_entities(
            BouyguesLineBinarySensor(coordinator, contract_id, description)
            for contract_id in new
            for description in BINARY_SENSORS
            if description.exists_fn(coordinator.data.lines[contract_id])
        )

    _add_new_lines()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_lines))


class BouyguesLineBinarySensor(BouyguesLineEntity, BinarySensorEntity):
    """A binary sensor of one mobile line."""

    entity_description: LineBinarySensorDescription

    def __init__(
        self,
        coordinator: BouyguesTelecomCoordinator,
        contract_id: str,
        description: LineBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, contract_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the state."""
        return self.entity_description.is_on_fn(self.line)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.line)
