"""The Bouygues Telecom integration."""

from __future__ import annotations

import aiohttp
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import import_cookies
from .const import CONF_COOKIES
from .coordinator import BouyguesTelecomConfigEntry, BouyguesTelecomCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(
    hass: HomeAssistant, entry: BouyguesTelecomConfigEntry
) -> bool:
    """Set up Bouygues Telecom from a config entry."""
    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
    import_cookies(session, entry.data[CONF_COOKIES])
    coordinator = BouyguesTelecomCoordinator(hass, entry, session)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: BouyguesTelecomConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
