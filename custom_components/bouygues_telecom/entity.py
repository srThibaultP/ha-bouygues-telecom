"""Base entities of the Bouygues Telecom integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CUSTOMER_AREA_URL, DOMAIN, MANUFACTURER
from .coordinator import BouyguesTelecomCoordinator, LineData


class BouyguesLineEntity(CoordinatorEntity[BouyguesTelecomCoordinator]):
    """An entity of one mobile line."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: BouyguesTelecomCoordinator, contract_id: str, key: str
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._contract_id = contract_id
        self._attr_unique_id = f"{contract_id}_{key}"
        contract = coordinator.data.lines[contract_id].contract
        number = contract.get("numeroTel") or contract_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, contract_id)},
            name=(contract.get("personnalisationContrat") or {}).get("nom") or number,
            manufacturer=MANUFACTURER,
            model=(contract.get("abonnement") or {}).get("libelle"),
            serial_number=number,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=CUSTOMER_AREA_URL,
        )

    @property
    def available(self) -> bool:
        """Unavailable once the line is no longer on the account."""
        return super().available and self._contract_id in self.coordinator.data.lines

    @property
    def line(self) -> LineData:
        """Data of this line."""
        return self.coordinator.data.lines[self._contract_id]


class BouyguesAccountEntity(CoordinatorEntity[BouyguesTelecomCoordinator]):
    """An entity of the customer account (billing)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BouyguesTelecomCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.person_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.person_id)},
            translation_key="account",
            manufacturer=MANUFACTURER,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=CUSTOMER_AREA_URL,
        )
