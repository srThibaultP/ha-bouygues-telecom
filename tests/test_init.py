"""Tests of setup, polling and device handling."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.bouygues_telecom.api import (
    CannotConnect,
    CodeRequired,
    InvalidAuth,
    import_cookies,
)
from custom_components.bouygues_telecom.const import CONF_COOKIES, DOMAIN

from .conftest import CONTRACTS, MAIN_ID, PERSON_ID, SECONDARY_ID

MAIN_DATA_USED = "sensor.my_phone_data_used"
SECONDARY_DATA_USED = "sensor.0600000002_data_used"


def _line_ids(device_registry: dr.DeviceRegistry, entry: MockConfigEntry) -> set[str]:
    return {
        identifier
        for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id)
        for domain, identifier in device.identifiers
        if domain == DOMAIN
    }


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(timedelta(minutes=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The entry loads, creates entities and unloads."""
    assert init_integration.state is ConfigEntryState.LOADED
    assert hass.states.get(MAIN_DATA_USED) is not None
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    assert init_integration.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize("error", [CodeRequired("code"), InvalidAuth()])
async def test_setup_auth_failure_starts_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    error: Exception,
) -> None:
    """An expired session that needs a code asks the user to sign in again."""
    mock_client.async_get_mobile_contracts.side_effect = error
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_setup_retry_on_connection_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Network errors retry the setup later."""
    mock_client.async_get_mobile_contracts.side_effect = CannotConnect("down")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_update_error_marks_unavailable(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Entities become unavailable when polling fails, and recover."""
    mock_client.async_get_mobile_contracts.side_effect = CannotConnect("down")
    await _tick(hass, freezer)
    assert hass.states.get(MAIN_DATA_USED).state == "unavailable"

    mock_client.async_get_mobile_contracts.side_effect = lambda: CONTRACTS
    await _tick(hass, freezer)
    assert hass.states.get(MAIN_DATA_USED).state != "unavailable"


async def test_line_error_keeps_previous_data(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A transient error on one line keeps its last reading available."""
    before = hass.states.get(MAIN_DATA_USED).state
    mock_client.async_get_consumption.side_effect = CannotConnect("HTTP 503")
    await _tick(hass, freezer)
    assert hass.states.get(MAIN_DATA_USED).state == before
    assert hass.states.get(SECONDARY_DATA_USED).state != "unavailable"


async def test_line_error_without_previous_data(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Without a previous reading, a line error still fails the refresh."""
    mock_client.async_get_consumption.side_effect = CannotConnect("HTTP 503")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_details_are_cached(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Plan, options and invoices are only fetched every few hours."""
    assert mock_client.async_get_invoices.call_count == 1
    await _tick(hass, freezer)
    assert mock_client.async_get_invoices.call_count == 1
    assert mock_client.async_get_consumption.call_count == 4

    freezer.tick(timedelta(hours=6))
    await _tick(hass, freezer)
    assert mock_client.async_get_invoices.call_count == 2


async def test_cookies_are_persisted(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Rotated CAS cookies are written back to the entry."""
    session = init_integration.runtime_data.session
    session.cookie_jar.clear()

    new = [
        {
            "name": "CASTGC",
            "value": "rotated",
            "domain": "www.mon-compte.bouyguestelecom.fr",
            "path": "/cas",
        }
    ]
    import_cookies(session, new)
    await init_integration.runtime_data.async_refresh()
    assert init_integration.data[CONF_COOKIES] == new


async def test_lines_come_and_go(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    device_registry: dr.DeviceRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A removed line loses its device; a new one gets entities."""
    assert _line_ids(device_registry, init_integration) == {
        PERSON_ID,
        MAIN_ID,
        SECONDARY_ID,
    }

    mock_client.async_get_mobile_contracts.side_effect = lambda: [CONTRACTS[0]]
    await _tick(hass, freezer)
    assert _line_ids(device_registry, init_integration) == {PERSON_ID, MAIN_ID}
    assert hass.states.get(SECONDARY_DATA_USED) is None

    mock_client.async_get_mobile_contracts.side_effect = lambda: CONTRACTS
    await _tick(hass, freezer)
    assert _line_ids(device_registry, init_integration) == {
        PERSON_ID,
        MAIN_ID,
        SECONDARY_ID,
    }


async def test_account_without_invoice(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Invoice sensors are unknown until the first invoice."""
    mock_client.async_get_invoices.side_effect = list
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert (
        hass.states.get("sensor.bouygues_telecom_account_last_invoice_amount").state
        == "unknown"
    )
