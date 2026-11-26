"""Sensors of the Bouygues Telecom integration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfInformation,
    UnitOfMass,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import helpers as h
from .coordinator import (
    BouyguesTelecomConfigEntry,
    BouyguesTelecomCoordinator,
    LineData,
)
from .entity import BouyguesAccountEntity, BouyguesLineEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class LineSensorDescription(SensorEntityDescription):
    """Description of a sensor of a mobile line."""

    value_fn: Callable[[LineData], Any]
    exists_fn: Callable[[LineData], bool] = lambda _: True
    last_reset_fn: Callable[[LineData], datetime | None] | None = None
    attributes_fn: Callable[[LineData], Mapping[str, Any]] | None = None


@dataclass(frozen=True, kw_only=True)
class AccountSensorDescription(SensorEntityDescription):
    """Description of a sensor of the customer account."""

    value_fn: Callable[[dict[str, Any]], Any]


_DATA: dict[str, Any] = {
    "device_class": SensorDeviceClass.DATA_SIZE,
    "native_unit_of_measurement": UnitOfInformation.BYTES,
    "suggested_unit_of_measurement": UnitOfInformation.GIBIBYTES,
    "suggested_display_precision": 2,
}
_DURATION: dict[str, Any] = {
    "device_class": SensorDeviceClass.DURATION,
    "native_unit_of_measurement": UnitOfTime.SECONDS,
    "suggested_unit_of_measurement": UnitOfTime.MINUTES,
    "suggested_display_precision": 0,
}
_EUR: dict[str, Any] = {
    "device_class": SensorDeviceClass.MONETARY,
    "native_unit_of_measurement": "EUR",
    "suggested_display_precision": 2,
}

LINE_SENSORS: tuple[LineSensorDescription, ...] = (
    LineSensorDescription(
        key="data_used",
        translation_key="data_used",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: h.counter_value(h.data_counter(d, "PRINCIPALE"), "used"),
        last_reset_fn=h.period_start,
        **_DATA,
    ),
    LineSensorDescription(
        key="data_allowance",
        translation_key="data_allowance",
        value_fn=lambda d: h.counter_value(h.data_counter(d, "PRINCIPALE"), "total"),
        **_DATA,
    ),
    LineSensorDescription(
        key="data_remaining",
        translation_key="data_remaining",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: h.counter_remaining(h.data_counter(d, "PRINCIPALE")),
        **_DATA,
    ),
    LineSensorDescription(
        key="data_used_percent",
        translation_key="data_used_percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: h.counter_percent(h.data_counter(d, "PRINCIPALE")),
    ),
    LineSensorDescription(
        key="roaming_data_used",
        translation_key="roaming_data_used",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: h.counter_value(h.data_counter(d, "ADDITIONNELLE"), "used"),
        exists_fn=lambda d: h.data_counter(d, "ADDITIONNELLE") is not None,
        last_reset_fn=h.period_start,
        entity_registry_enabled_default=False,
        **_DATA,
    ),
    LineSensorDescription(
        key="roaming_data_allowance",
        translation_key="roaming_data_allowance",
        value_fn=lambda d: h.counter_value(h.data_counter(d, "ADDITIONNELLE"), "total"),
        exists_fn=lambda d: h.data_counter(d, "ADDITIONNELLE") is not None,
        entity_registry_enabled_default=False,
        **_DATA,
    ),
    LineSensorDescription(
        key="calls_used",
        translation_key="calls_used",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: h.counter_value(h.voice_counter(d), "used"),
        exists_fn=lambda d: h.voice_counter(d) is not None,
        last_reset_fn=h.period_start,
        **_DURATION,
    ),
    LineSensorDescription(
        key="calls_allowance",
        translation_key="calls_allowance",
        value_fn=lambda d: h.counter_value(h.voice_counter(d), "total"),
        # Unlimited plans report -1.
        exists_fn=lambda d: (h.counter_value(h.voice_counter(d), "total") or 0) > 0,
        **_DURATION,
    ),
    LineSensorDescription(
        key="out_of_plan",
        translation_key="out_of_plan",
        value_fn=lambda d: d.consumption.get("horsForfait", {}).get(
            "montantTotalHorsForfaitTTC"
        ),
        attributes_fn=lambda d: {
            "details": h.strip_links(d.extras["out_of_plan"].get("sections", []))
        },
        **_EUR,
    ),
    LineSensorDescription(
        key="data_top_ups",
        translation_key="data_top_ups",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.extras["top_ups"].get("items", [])),
        attributes_fn=lambda d: {
            "top_ups": h.strip_links(d.extras["top_ups"].get("items", []))
        },
    ),
    LineSensorDescription(
        key="carbon_footprint",
        translation_key="carbon_footprint",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.GRAMS,
        suggested_unit_of_measurement=UnitOfMass.KILOGRAMS,
        suggested_display_precision=2,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: d.consumption.get("impactCarbone", {}).get(
            "equivalentGrammeCO2"
        ),
        exists_fn=lambda d: "impactCarbone" in d.consumption,
        last_reset_fn=h.period_start,
    ),
    LineSensorDescription(
        key="plan_price",
        translation_key="plan_price",
        value_fn=lambda d: h.main_offer(d).get("tarifRemiseTTC"),
        # Secondary lines (multi-SIM...) are billed on the main plan.
        exists_fn=lambda d: (h.main_offer(d).get("tarifTTC") or 0) > 0,
        attributes_fn=lambda d: {
            "plan": h.main_offer(d).get("libelleCommercial"),
            "price_without_discount": h.main_offer(d).get("tarifTTC"),
            "discount": h.main_offer(d).get("montantRemiseTTC"),
        },
        **_EUR,
    ),
    LineSensorDescription(
        key="options",
        translation_key="options",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: len(h.subscribed_options(d)),
        attributes_fn=lambda d: {"options": h.subscribed_options(d)},
    ),
    LineSensorDescription(
        key="phone_renewal_date",
        translation_key="phone_renewal_date",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda d: h.parse_date(
            (h.renewal(d) or {}).get("dateEligibilitePrevisionnelleRenouvellement")
        ),
        exists_fn=lambda d: bool(
            (h.renewal(d) or {}).get("dateEligibilitePrevisionnelleRenouvellement")
        ),
    ),
    LineSensorDescription(
        key="renewal_date",
        translation_key="renewal_date",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda d: h.parse_date(d.consumption.get("dateReinitialisation")),
    ),
    LineSensorDescription(
        key="last_reading",
        translation_key="last_reading",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: h.paris_datetime(d.consumption.get("dateReleve")),
    ),
)

ACCOUNT_SENSORS: tuple[AccountSensorDescription, ...] = (
    AccountSensorDescription(
        key="last_invoice_amount",
        translation_key="last_invoice_amount",
        value_fn=lambda i: i.get("mntTotFacture"),
        **_EUR,
    ),
    AccountSensorDescription(
        key="last_invoice_date",
        translation_key="last_invoice_date",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda i: h.invoice_date(i.get("dateFacturation")),
    ),
    AccountSensorDescription(
        key="invoice_due_date",
        translation_key="invoice_due_date",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda i: h.invoice_date(i.get("dateLimitePaieFacture")),
    ),
    AccountSensorDescription(
        key="balance",
        translation_key="balance",
        value_fn=lambda i: i.get("soldeApresFacture"),
        **_EUR,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BouyguesTelecomConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors, adding lines that appear on the account later on."""
    coordinator = entry.runtime_data
    async_add_entities(
        BouyguesAccountSensor(coordinator, description)
        for description in ACCOUNT_SENSORS
    )
    known: set[str] = set()

    @callback
    def _add_new_lines() -> None:
        current = set(coordinator.data.lines)
        # Forget removed lines so they get entities again if they come back.
        known.intersection_update(current)
        new = current - known
        known.update(new)
        async_add_entities(
            BouyguesLineSensor(coordinator, contract_id, description)
            for contract_id in new
            for description in LINE_SENSORS
            if description.exists_fn(coordinator.data.lines[contract_id])
        )

    _add_new_lines()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_lines))


class BouyguesLineSensor(BouyguesLineEntity, SensorEntity):
    """A sensor of one mobile line."""

    entity_description: LineSensorDescription

    def __init__(
        self,
        coordinator: BouyguesTelecomCoordinator,
        contract_id: str,
        description: LineSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, contract_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the state."""
        return self.entity_description.value_fn(self.line)

    @property
    def last_reset(self) -> datetime | None:
        """Start of the billing period for counters that reset."""
        if self.entity_description.last_reset_fn is None:
            return None
        return self.entity_description.last_reset_fn(self.line)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.line)


class BouyguesAccountSensor(BouyguesAccountEntity, SensorEntity):
    """A sensor of the latest invoice."""

    entity_description: AccountSensorDescription

    def __init__(
        self,
        coordinator: BouyguesTelecomCoordinator,
        description: AccountSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> date | float | None:
        """Return the state, unknown while the account has no invoice."""
        if not self.coordinator.data.invoices:
            return None
        value: date | float | None = self.entity_description.value_fn(
            self.coordinator.data.invoices[0]
        )
        return value
