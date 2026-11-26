"""Config flow for Bouygues Telecom: password, then a one-time code."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    BouyguesTelecomClient,
    BouyguesTelecomError,
    CannotConnect,
    InvalidAuth,
    InvalidCode,
    LoginFlow,
    SessionExpired,
    export_cookies,
)
from .const import CONF_COOKIES, CONF_PERSON_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_CONTACT = "contact"
CONF_CODE = "code"


class BouyguesTelecomConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Bouygues Telecom."""

    VERSION = 1

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._login: LoginFlow | None = None
        self._username = ""
        self._contacts: dict[str, str] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the customer-area credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = user_input[CONF_USERNAME].strip()
            self._session = async_create_clientsession(
                self.hass, cookie_jar=aiohttp.CookieJar()
            )
            self._login = LoginFlow(self._session)
            try:
                contacts = await self._login.start(
                    self._username, user_input[CONF_PASSWORD]
                )
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during login")
                errors["base"] = "unknown"
            else:
                if contacts is None:
                    return await self._async_finish()
                self._contacts = {
                    key: value
                    for key, value in (
                        ("sms", contacts.phone),
                        ("email", contacts.email),
                    )
                    if value
                }
                if len(self._contacts) > 1:
                    return await self.async_step_contact()
                if self._contacts:
                    return await self.async_step_contact(
                        {CONF_CONTACT: next(iter(self._contacts))}
                    )
                return await self.async_step_code()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_USERNAME): TextSelector(
                            TextSelectorConfig(
                                type=TextSelectorType.EMAIL, autocomplete="username"
                            )
                        ),
                        vol.Required(CONF_PASSWORD): TextSelector(
                            TextSelectorConfig(
                                type=TextSelectorType.PASSWORD,
                                autocomplete="current-password",
                            )
                        ),
                    }
                ),
                {CONF_USERNAME: self._username},
            ),
            errors=errors,
        )

    async def async_step_contact(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose where the one-time code is sent."""
        errors: dict[str, str] = {}
        if user_input is not None and self._login is not None:
            try:
                await self._login.send_code(self._contacts[user_input[CONF_CONTACT]])
            except BouyguesTelecomError:
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_code()

        return self.async_show_form(
            step_id="contact",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONTACT, default=next(iter(self._contacts), "sms")
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=list(self._contacts),
                            translation_key=CONF_CONTACT,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            description_placeholders={
                "phone": self._contacts.get("sms", "-"),
                "email": self._contacts.get("email", "-"),
            },
            errors=errors,
        )

    async def async_step_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter the one-time code."""
        errors: dict[str, str] = {}
        if user_input is not None and self._login is not None:
            try:
                await self._login.verify(user_input[CONF_CODE])
            except InvalidCode:
                errors["base"] = "invalid_code"
            except BouyguesTelecomError:
                errors["base"] = "cannot_connect"
            else:
                return await self._async_finish()

        return self.async_show_form(
            step_id="code",
            data_schema=vol.Schema({vol.Required(CONF_CODE): str}),
            errors=errors,
        )

    async def _async_finish(self) -> ConfigFlowResult:
        if self._session is None:
            return self.async_abort(reason="unknown")
        client = BouyguesTelecomClient(self._session)
        try:
            # Proves the saved session can mint tokens without a new code.
            await client.async_refresh_token()
        except SessionExpired:
            return self.async_abort(reason="silent_renew_failed")
        except BouyguesTelecomError:
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(client.person_id)
        data = {
            CONF_USERNAME: self._username,
            CONF_PERSON_ID: client.person_id,
            CONF_COOKIES: export_cookies(self._session),
        }
        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data_updates=data
            )
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=self._username, data=data)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """The CAS session expired: log in again."""
        self._username = entry_data[CONF_USERNAME]
        return await self.async_step_user()
