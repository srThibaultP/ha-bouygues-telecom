"""Consumption sensors for Bouygues Telecom mobile lines."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import (
    BouyguesTelecomConfigEntry,
    BouyguesTelecomCoordinator,
    LineData,
)

PARALLEL_UPDATES = 0


def _data_counter(line: LineData, counter_type: str) -> dict[str, Any] | None:
    return next(
        (
            c
            for c in line.consumption.get("compteursConsoDATA", [])
            if c.get("type") == counter_type
        ),
        None,
    )


def _voice_counter(line: LineData) -> dict[str, Any] | None:
    return next(
        (
            c
            for c in line.consumption.get("compteursConsoVOIX", [])
            if c.get("type") == "PRINCIPALE"
        ),
        None,
    )


def _field(counter: dict[str, Any] | None, key: str) -> float | None:
    if counter is None or counter.get(key) is None or counter[key] < 0:
        return None
    return float(counter[key])


def _remaining(counter: dict[str, Any] | None) -> float | None:
    used, total = (
        _field(counter, "quantiteConsommee"),
        _field(counter, "quantiteTotale"),
    )
    if used is None or total is None:
        return None
    return max(total - used, 0.0)


def _percent(counter: dict[str, Any] | None) -> float | None:
    used, total = (
        _field(counter, "quantiteConsommee"),
        _field(counter, "quantiteTotale"),
    )
    if used is None or not total:
        return None
    return round(used / total * 100, 1)


def _date(value: str | None) -> date | None:
    return dt_util.parse_date(value) if value else None


def _paris_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.get_time_zone("Europe/Paris"))
    return parsed


def _period_start(line: LineData) -> datetime | None:
    start = _date(line.consumption.get("dateDebutConso"))
    if start is None:
        return None
    return datetime.combine(
        start, datetime.min.time(), dt_util.get_time_zone("Europe/Paris")
    )


@dataclass(frozen=True, kw_only=True)
class BouyguesSensorDescription(SensorEntityDescription):
    """Sensor description with value accessors."""

    value_fn: Callable[[LineData], Any]
    exists_fn: Callable[[LineData], bool] = lambda _: True
    last_reset_fn: Callable[[LineData], datetime | None] | None = None


_DATA = {
    "device_class": SensorDeviceClass.DATA_SIZE,
    "native_unit_of_measurement": UnitOfInformation.BYTES,
    "suggested_unit_of_measurement": UnitOfInformation.GIBIBYTES,
    "suggested_display_precision": 2,
}

SENSORS: tuple[BouyguesSensorDescription, ...] = (
    BouyguesSensorDescription(
        key="data_used",
        translation_key="data_used",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: _field(_data_counter(d, "PRINCIPALE"), "quantiteConsommee"),
        last_reset_fn=_period_start,
        **_DATA,
    ),
    BouyguesSensorDescription(
        key="data_allowance",
        translation_key="data_allowance",
        value_fn=lambda d: _field(_data_counter(d, "PRINCIPALE"), "quantiteTotale"),
        **_DATA,
    ),
    BouyguesSensorDescription(
        key="data_remaining",
        translation_key="data_remaining",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _remaining(_data_counter(d, "PRINCIPALE")),
        **_DATA,
    ),
    BouyguesSensorDescription(
        key="data_used_percent",
        translation_key="data_used_percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _percent(_data_counter(d, "PRINCIPALE")),
    ),
    BouyguesSensorDescription(
        key="roaming_data_used",
        translation_key="roaming_data_used",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: _field(
            _data_counter(d, "ADDITIONNELLE"), "quantiteConsommee"
        ),
        exists_fn=lambda d: _data_counter(d, "ADDITIONNELLE") is not None,
        last_reset_fn=_period_start,
        entity_registry_enabled_default=False,
        **_DATA,
    ),
    BouyguesSensorDescription(
        key="roaming_data_allowance",
        translation_key="roaming_data_allowance",
        value_fn=lambda d: _field(_data_counter(d, "ADDITIONNELLE"), "quantiteTotale"),
        exists_fn=lambda d: _data_counter(d, "ADDITIONNELLE") is not None,
        entity_registry_enabled_default=False,
        **_DATA,
    ),
    BouyguesSensorDescription(
        key="calls_used",
        translation_key="calls_used",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: _field(_voice_counter(d), "quantiteConsommee"),
        exists_fn=lambda d: _voice_counter(d) is not None,
        last_reset_fn=_period_start,
    ),
    BouyguesSensorDescription(
        key="calls_allowance",
        translation_key="calls_allowance",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value_fn=lambda d: _field(_voice_counter(d), "quantiteTotale"),
        # Unlimited plans report -1.
        exists_fn=lambda d: (_field(_voice_counter(d), "quantiteTotale") or 0) > 0,
    ),
    BouyguesSensorDescription(
        key="out_of_plan",
        translation_key="out_of_plan",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="EUR",
        suggested_display_precision=2,
        value_fn=lambda d: d.consumption.get("horsForfait", {}).get(
            "montantTotalHorsForfaitTTC"
        ),
    ),
    BouyguesSensorDescription(
        key="renewal_date",
        translation_key="renewal_date",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda d: _date(d.consumption.get("dateReinitialisation")),
    ),
    BouyguesSensorDescription(
        key="last_reading",
        translation_key="last_reading",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: _paris_datetime(d.consumption.get("dateReleve")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BouyguesTelecomConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for every mobile line."""
    coordinator = entry.runtime_data
    async_add_entities(
        BouyguesTelecomSensor(coordinator, contract_id, description)
        for contract_id, line in coordinator.data.items()
        for description in SENSORS
        if description.exists_fn(line)
    )


class BouyguesTelecomSensor(
    CoordinatorEntity[BouyguesTelecomCoordinator], SensorEntity
):
    """A consumption value of one mobile line."""

    _attr_has_entity_name = True
    entity_description: BouyguesSensorDescription

    def __init__(
        self,
        coordinator: BouyguesTelecomCoordinator,
        contract_id: str,
        description: BouyguesSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._contract_id = contract_id
        self._attr_unique_id = f"{contract_id}_{description.key}"
        contract = coordinator.data[contract_id].contract
        number = contract.get("numeroTel") or contract_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, contract_id)},
            name=(contract.get("personnalisationContrat") or {}).get("nom") or number,
            manufacturer="Bouygues Telecom",
            model=(contract.get("abonnement") or {}).get("libelle"),
            serial_number=number,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://www.bouyguestelecom.fr/mon-compte",
        )

    @property
    def available(self) -> bool:
        return super().available and self._contract_id in self.coordinator.data

    @property
    def _line(self) -> LineData:
        return self.coordinator.data[self._contract_id]

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._line)

    @property
    def last_reset(self) -> datetime | None:
        if self.entity_description.last_reset_fn is None:
            return None
        return self.entity_description.last_reset_fn(self._line)
