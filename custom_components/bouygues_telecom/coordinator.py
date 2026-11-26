"""Data update coordinator for Bouygues Telecom."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    BouyguesTelecomClient,
    BouyguesTelecomError,
    InvalidAuth,
    SessionExpired,
    export_cookies,
)
from .const import (
    CONF_COOKIES,
    CONF_PERSON_ID,
    DETAILS_INTERVAL,
    DOMAIN,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type BouyguesTelecomConfigEntry = ConfigEntry[BouyguesTelecomCoordinator]


@dataclass
class LineData:
    """One mobile line: contract, consumption and related data."""

    contract: dict[str, Any]
    consumption: dict[str, Any]
    extras: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class AccountData:
    """Everything polled for one customer account."""

    lines: dict[str, LineData]
    invoices: list[dict[str, Any]]


class BouyguesTelecomCoordinator(DataUpdateCoordinator[AccountData]):
    """Poll the mobile lines and invoices of a customer account."""

    config_entry: BouyguesTelecomConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BouyguesTelecomConfigEntry,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.session = session
        self.client = BouyguesTelecomClient(
            session, entry.data[CONF_USERNAME], entry.data.get(CONF_PASSWORD)
        )
        self.person_id: str = entry.data[CONF_PERSON_ID]
        self._details: dict[str, dict[str, Any]] = {}
        self._invoices: list[dict[str, Any]] = []
        self._details_fetched_at: datetime | None = None

    async def _async_update_data(self) -> AccountData:
        try:
            data = await self._async_fetch()
        except (SessionExpired, InvalidAuth) as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="session_expired"
            ) from err
        except BouyguesTelecomError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        finally:
            self._persist_cookies()
        self._remove_stale_devices(data)
        return data

    async def _async_fetch(self) -> AccountData:
        contracts = await self.client.async_get_mobile_contracts()
        refresh_details = (
            self._details_fetched_at is None
            or dt_util.utcnow() - self._details_fetched_at > DETAILS_INTERVAL
            or set(self._details) != {str(c["id"]) for c in contracts}
        )
        if refresh_details:
            self._details = {
                str(c["id"]): await self.client.async_get_contract_details(str(c["id"]))
                for c in contracts
            }
            self._invoices = await self.client.async_get_invoices()
            self._details_fetched_at = dt_util.utcnow()
        lines = {}
        for contract in contracts:
            contract_id = str(contract["id"])
            lines[contract_id] = LineData(
                contract=contract,
                consumption=await self.client.async_get_consumption(contract_id),
                extras=await self.client.async_get_contract_extras(contract_id),
                details=self._details[contract_id],
            )
        return AccountData(lines=lines, invoices=self._invoices)

    def _persist_cookies(self) -> None:
        # CAS may rotate its session cookie; keep the latest so a restart doesn't need a new code.
        cookies = export_cookies(self.session)
        if cookies and cookies != self.config_entry.data.get(CONF_COOKIES):
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONF_COOKIES: cookies},
            )

    def _remove_stale_devices(self, data: AccountData) -> None:
        registry = dr.async_get(self.hass)
        keep = {self.person_id, *data.lines}
        for device in dr.async_entries_for_config_entry(
            registry, self.config_entry.entry_id
        ):
            ids = {i for domain, i in device.identifiers if domain == DOMAIN}
            if ids and not ids & keep:
                registry.async_update_device(
                    device.id, remove_config_entry_id=self.config_entry.entry_id
                )
