"""Data update coordinator for Bouygues Telecom."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    BouyguesTelecomClient,
    BouyguesTelecomError,
    SessionExpired,
    export_cookies,
)
from .const import CONF_COOKIES, DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

type BouyguesTelecomConfigEntry = ConfigEntry[BouyguesTelecomCoordinator]


@dataclass
class LineData:
    """Contract description and its current consumption."""

    contract: dict[str, Any]
    consumption: dict[str, Any]


class BouyguesTelecomCoordinator(DataUpdateCoordinator[dict[str, LineData]]):
    """Poll the consumption of every mobile line of the account."""

    config_entry: BouyguesTelecomConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BouyguesTelecomConfigEntry,
        session: aiohttp.ClientSession,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.session = session
        self.client = BouyguesTelecomClient(session)

    async def _async_update_data(self) -> dict[str, LineData]:
        try:
            contracts = await self.client.async_get_mobile_contracts()
            data = {
                str(c["id"]): LineData(
                    c, await self.client.async_get_consumption(str(c["id"]))
                )
                for c in contracts
            }
        except SessionExpired as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="session_expired"
            ) from err
        except BouyguesTelecomError as err:
            raise UpdateFailed(str(err)) from err
        finally:
            self._persist_cookies()
        return data

    def _persist_cookies(self) -> None:
        # CAS may rotate its session cookie; keep the latest so a restart doesn't need a new OTP.
        cookies = export_cookies(self.session)
        if cookies and cookies != self.config_entry.data.get(CONF_COOKIES):
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONF_COOKIES: cookies},
            )
