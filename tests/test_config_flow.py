"""Tests of the config flow."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant.common import MockConfigEntry

from custom_components.bouygues_telecom.api import (
    CannotConnect,
    InvalidAuth,
    InvalidCode,
    OtpContacts,
    SessionExpired,
)
from custom_components.bouygues_telecom.const import (
    CONF_COOKIES,
    CONF_PERSON_ID,
    DOMAIN,
)

from .conftest import COOKIES, PASSWORD, PERSON_ID, USERNAME

CREDENTIALS = {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
FLOW = "custom_components.bouygues_telecom.config_flow"


@pytest.fixture(autouse=True)
def mock_setup_entry() -> Generator[AsyncMock]:
    """Do not set the entry up after the flow."""
    with patch(
        "custom_components.bouygues_telecom.async_setup_entry", return_value=True
    ) as setup:
        yield setup


@pytest.fixture
def mock_login() -> Generator[MagicMock]:
    """Patch the interactive login."""
    login = MagicMock()
    login.start = AsyncMock(return_value=OtpContacts("06 ** 01", "u**@e**.com"))
    login.send_code = AsyncMock()
    login.verify = AsyncMock()
    with patch(f"{FLOW}.LoginFlow", return_value=login):
        yield login


@pytest.fixture
def mock_flow_client() -> Generator[MagicMock]:
    """Patch the client that checks silent renewal and the exported cookies."""
    client = MagicMock()
    client.async_refresh_token = AsyncMock()
    client.person_id = PERSON_ID
    with (
        patch(f"{FLOW}.BouyguesTelecomClient", return_value=client),
        patch(f"{FLOW}.export_cookies", return_value=COOKIES),
    ):
        yield client


async def _start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    flow_id: str = result["flow_id"]
    return flow_id


@pytest.mark.usefixtures("mock_flow_client")
async def test_full_flow(hass: HomeAssistant, mock_login: MagicMock) -> None:
    """Password, contact choice and code create the entry."""
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, CREDENTIALS)
    assert result["step_id"] == "contact"
    assert result["description_placeholders"] == {
        "phone": "06 ** 01",
        "email": "u**@e**.com",
    }

    result = await hass.config_entries.flow.async_configure(
        flow_id, {"contact": "email"}
    )
    mock_login.send_code.assert_awaited_once_with("u**@e**.com")
    assert result["step_id"] == "code"

    result = await hass.config_entries.flow.async_configure(flow_id, {"code": "123456"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == USERNAME
    assert result["result"].unique_id == PERSON_ID
    assert result["data"] == {
        **CREDENTIALS,
        CONF_PERSON_ID: PERSON_ID,
        CONF_COOKIES: COOKIES,
    }


@pytest.mark.usefixtures("mock_flow_client")
async def test_trusted_device_skips_code(
    hass: HomeAssistant, mock_login: MagicMock
) -> None:
    """No code is asked when CAS trusts the device."""
    mock_login.start.return_value = None
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, CREDENTIALS)
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("mock_flow_client")
async def test_single_contact_sends_code(
    hass: HomeAssistant, mock_login: MagicMock
) -> None:
    """With one contact the code is sent without asking."""
    mock_login.start.return_value = OtpContacts("06 ** 01", None)
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, CREDENTIALS)
    mock_login.send_code.assert_awaited_once_with("06 ** 01")
    assert result["step_id"] == "code"


@pytest.mark.usefixtures("mock_flow_client")
async def test_code_already_sent(hass: HomeAssistant, mock_login: MagicMock) -> None:
    """CAS may skip the contact choice and send the code straight away."""
    mock_login.start.return_value = OtpContacts(None, None)
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, CREDENTIALS)
    mock_login.send_code.assert_not_awaited()
    assert result["step_id"] == "code"


@pytest.mark.usefixtures("mock_flow_client")
@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (InvalidAuth(), "invalid_auth"),
        (CannotConnect("down"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_login_errors(
    hass: HomeAssistant, mock_login: MagicMock, error: Exception, reason: str
) -> None:
    """Login errors are shown and the user can retry."""
    mock_login.start.side_effect = error
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, CREDENTIALS)
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": reason}

    mock_login.start.side_effect = None
    mock_login.start.return_value = None
    result = await hass.config_entries.flow.async_configure(flow_id, CREDENTIALS)
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("mock_flow_client")
async def test_send_code_error(hass: HomeAssistant, mock_login: MagicMock) -> None:
    """A failure to send the code keeps the contact step."""
    mock_login.send_code.side_effect = CannotConnect("down")
    flow_id = await _start(hass)
    await hass.config_entries.flow.async_configure(flow_id, CREDENTIALS)
    result = await hass.config_entries.flow.async_configure(flow_id, {"contact": "sms"})
    assert result["step_id"] == "contact"
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.usefixtures("mock_flow_client")
@pytest.mark.parametrize(
    ("error", "reason"),
    [(InvalidCode(), "invalid_code"), (CannotConnect("down"), "cannot_connect")],
)
async def test_code_errors(
    hass: HomeAssistant, mock_login: MagicMock, error: Exception, reason: str
) -> None:
    """A wrong code can be typed again."""
    mock_login.verify.side_effect = error
    flow_id = await _start(hass)
    await hass.config_entries.flow.async_configure(flow_id, CREDENTIALS)
    await hass.config_entries.flow.async_configure(flow_id, {"contact": "sms"})
    result = await hass.config_entries.flow.async_configure(flow_id, {"code": "000000"})
    assert result["step_id"] == "code"
    assert result["errors"] == {"base": reason}


@pytest.mark.usefixtures("mock_login")
@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (SessionExpired("no"), "silent_renew_failed"),
        (CannotConnect("down"), "cannot_connect"),
    ],
)
async def test_silent_renewal_check(
    hass: HomeAssistant, mock_flow_client: MagicMock, error: Exception, reason: str
) -> None:
    """The flow aborts if the session cannot mint tokens on its own."""
    mock_flow_client.async_refresh_token.side_effect = error
    flow_id = await _start(hass)
    await hass.config_entries.flow.async_configure(flow_id, CREDENTIALS)
    await hass.config_entries.flow.async_configure(flow_id, {"contact": "sms"})
    result = await hass.config_entries.flow.async_configure(flow_id, {"code": "1"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason


@pytest.mark.usefixtures("mock_flow_client")
async def test_already_configured(
    hass: HomeAssistant, mock_login: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An account is only added once."""
    mock_config_entry.add_to_hass(hass)
    mock_login.start.return_value = None
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, CREDENTIALS)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("mock_flow_client")
@pytest.mark.parametrize("source", ["reauth", "reconfigure"])
async def test_reauth_and_reconfigure(
    hass: HomeAssistant,
    mock_login: MagicMock,
    mock_config_entry: MockConfigEntry,
    source: str,
) -> None:
    """Signing in again keeps the trusted-device cookie and updates the entry."""
    mock_config_entry.add_to_hass(hass)
    mock_login.start.return_value = None
    result = await getattr(mock_config_entry, f"start_{source}_flow")(hass)
    assert result["step_id"] == "user"

    with patch(f"{FLOW}.import_cookies") as import_cookies:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: "new"}
        )
    assert [c["name"] for c in import_cookies.call_args.args[1]] == ["MFATRUSTED"]
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == f"{source}_successful"
    assert mock_config_entry.data[CONF_PASSWORD] == "new"


@pytest.mark.usefixtures("mock_login")
async def test_reauth_wrong_account(
    hass: HomeAssistant,
    mock_login: MagicMock,
    mock_flow_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Another account cannot replace the configured one."""
    mock_config_entry.add_to_hass(hass)
    mock_login.start.return_value = None
    mock_flow_client.person_id = "other"
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
