"""Client for the Bouygues Telecom customer-area API.

Auth mirrors the web customer area: Apereo CAS login (password + OTP) behind an
OAuth2 PKCE flow. The web client gets no refresh token, so the long-lived CAS
"remember me" session cookies are kept and used to mint access tokens silently
with ``prompt=none``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from http.cookies import Morsel
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import aiohttp
from yarl import URL

from .const import (
    API_URL,
    AUTHORIZE_URL,
    CAS_ORIGIN,
    CLIENT_ID,
    REDIRECT_URI,
    TOKEN_URL,
    USER_AGENT,
)

_CALLBACK_PREFIX = REDIRECT_URI.split("//callback")[0]
_TIMEOUT = aiohttp.ClientTimeout(total=30)
_MAX_HOPS = 15


class BouyguesTelecomError(Exception):
    """Base error."""


class CannotConnect(BouyguesTelecomError):
    """Network or unexpected server error."""


class InvalidAuth(BouyguesTelecomError):
    """Login or password rejected."""


class InvalidCode(BouyguesTelecomError):
    """OTP rejected."""


class SessionExpired(BouyguesTelecomError):
    """The saved CAS session can no longer mint tokens; a new login is needed."""


@dataclass
class OtpContacts:
    """Masked destinations the OTP can be sent to."""

    phone: str | None
    email: str | None


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "form":
            self._current = {"action": attributes.get("action"), "inputs": []}
            self.forms.append(self._current)
        elif tag in ("input", "button") and self._current is not None:
            self._current["inputs"].append(attributes)

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._current = None


def _first_form(html: str) -> dict[str, Any] | None:
    parser = _FormParser()
    parser.feed(html)
    return parser.forms[0] if parser.forms else None


def _form_fields(form: dict[str, Any]) -> dict[str, str]:
    # Only the "submit" event button: sending _eventId_regenerate too makes CAS resend the code.
    return {
        i["name"]: i.get("value") or ""
        for i in form["inputs"]
        if i.get("name")
        and (i.get("type") != "submit" or i["name"] == "_eventId_submit")
    }


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _authorize_url(challenge: str, silent: bool) -> str:
    query = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": secrets.token_urlsafe(12),
        "nonce": secrets.token_urlsafe(12),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "tmpl": "sasUnique",
    }
    if silent:
        query["prompt"] = "none"
    return f"{AUTHORIZE_URL}?{urlencode(query)}"


def _jwt_claims(token: str) -> dict[str, Any]:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


class _Browser:
    """Minimal redirect-following client that stops at the OAuth redirect_uri."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session

    async def go(
        self, url: str, data: dict[str, str] | None = None, referer: str | None = None
    ) -> tuple[str, str, str | None]:
        """Return (final_url, html, callback_url)."""
        method = "POST" if data is not None else "GET"
        for _ in range(_MAX_HOPS):
            headers = {"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"}
            if method == "POST":
                headers["Origin"] = CAS_ORIGIN
            if referer:
                headers["Referer"] = referer
            try:
                async with self.session.request(
                    method,
                    url,
                    data=data,
                    headers=headers,
                    allow_redirects=False,
                    timeout=_TIMEOUT,
                ) as resp:
                    location = resp.headers.get("Location")
                    status = resp.status
                    html = "" if location else await resp.text()
            except (aiohttp.ClientError, TimeoutError) as err:
                raise CannotConnect(str(err)) from err
            if location and status in (301, 302, 303, 307, 308):
                next_url = urljoin(url, location)
                if next_url.startswith(_CALLBACK_PREFIX):
                    return url, "", next_url
                url, method, data = next_url, "GET", None
                continue
            refresh = re.search(
                r'http-equiv="refresh" content="0;\s*URL=([^"]+)"', html
            )
            if refresh:
                url, method, data = refresh.group(1).replace("&amp;", "&"), "GET", None
                continue
            if status >= 500:
                raise CannotConnect(f"HTTP {status} at {url.split('?')[0]}")
            return url, html, None
        raise CannotConnect("too many redirects")


async def _exchange_code(
    session: aiohttp.ClientSession, callback_url: str, verifier: str
) -> dict[str, Any]:
    query = parse_qs(urlparse(callback_url).query)
    if "code" not in query:
        raise SessionExpired(query.get("error", ["no_code"])[0])
    try:
        async with session.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": query["code"][0],
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "code_verifier": verifier,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=_TIMEOUT,
        ) as resp:
            if resp.status != 200:
                raise CannotConnect(f"token endpoint HTTP {resp.status}")
            token = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError) as err:
        raise CannotConnect(str(err)) from err
    token["expires_at"] = time.time() + int(token.get("expires_in") or 3600)
    return token


def export_cookies(session: aiohttp.ClientSession) -> list[dict[str, str]]:
    """Serialize the Bouygues cookies of a session's jar."""
    return [
        {
            "name": m.key,
            "value": m.value,
            "domain": m["domain"],
            "path": m["path"] or "/",
        }
        for m in session.cookie_jar
        if "bouyguestelecom.fr" in (m["domain"] or "")
    ]


def import_cookies(
    session: aiohttp.ClientSession, cookies: list[dict[str, str]]
) -> None:
    """Load cookies previously produced by export_cookies."""
    for cookie in cookies:
        morsel: Morsel[str] = Morsel()
        morsel.set(cookie["name"], cookie["value"], cookie["value"])
        morsel["domain"] = cookie["domain"]
        morsel["path"] = cookie["path"]
        host = cookie["domain"].lstrip(".")
        session.cookie_jar.update_cookies(
            {cookie["name"]: morsel}, URL(f"https://{host}/")
        )


class LoginFlow:
    """Interactive login: password, OTP destination choice, then OTP code."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._browser = _Browser(session)
        self._verifier, challenge = _pkce()
        self._authorize = _authorize_url(challenge, silent=False)
        self._url = ""
        self._html = ""
        self.token: dict[str, Any] | None = None

    async def start(self, username: str, password: str) -> OtpContacts | None:
        """Submit credentials. Return OTP contacts, or None if no OTP was asked."""
        url, html, callback = await self._browser.go(self._authorize)
        if callback:
            self.token = await _exchange_code(self._session, callback, self._verifier)
            return None
        form = _first_form(html)
        if form is None or "MireLogin" not in html:
            raise CannotConnect("login page not found")
        fields = _form_fields(form)
        fields.update(username=username, password=password, rememberMe="true")
        await self._post(url, form, fields)
        if "errorLogin: true" in self._html:
            raise InvalidAuth
        if self.token:
            return None
        if "OtpMethod" in self._html:
            phone = re.search(r"tel:\s*'([^']*)'", self._html)
            email = re.search(r"email:\s*'([^']*)'", self._html)
            return OtpContacts(
                phone.group(1) if phone and phone.group(1) else None,
                email.group(1) if email and email.group(1) else None,
            )
        if "OtpInput" in self._html:
            return OtpContacts(None, None)  # code already sent to the only contact
        raise CannotConnect("unexpected page after login")

    async def send_code(self, contact: str) -> None:
        """Ask CAS to send the OTP to one of the masked contacts."""
        form = _first_form(self._html)
        if form is None:
            raise CannotConnect("contact selection form not found")
        fields = _form_fields(form)
        fields["maskedValue"] = contact
        await self._post(self._url, form, fields)
        if "OtpInput" not in self._html:
            raise CannotConnect("code entry page not found")

    async def verify(self, code: str) -> dict[str, Any]:
        """Submit the OTP; return the OAuth token."""
        form = _first_form(self._html)
        if form is None:
            raise CannotConnect("code entry form not found")
        fields = _form_fields(form)
        fields["token"] = code.strip()
        await self._post(self._url, form, fields)
        if self.token is None:
            if "OtpInput" in self._html:
                raise InvalidCode
            raise CannotConnect("no authorization code after OTP")
        return self.token

    async def _post(
        self, url: str, form: dict[str, Any], fields: dict[str, str]
    ) -> None:
        target = urljoin(url, form["action"] or url)
        self._url, self._html, callback = await self._browser.go(
            target, fields, referer=url
        )
        if callback:
            self.token = await _exchange_code(self._session, callback, self._verifier)


class BouyguesTelecomClient:
    """Token management and API calls on top of a logged-in CAS session."""

    def __init__(
        self, session: aiohttp.ClientSession, token: dict[str, Any] | None = None
    ) -> None:
        self._session = session
        self._browser = _Browser(session)
        self._token = token

    @property
    def person_id(self) -> str:
        """Customer id from the last id_token."""
        if self._token is None:
            raise SessionExpired("no token")
        return str(_jwt_claims(self._token["id_token"])["id_personne"])

    async def async_refresh_token(self) -> None:
        """Mint a new access token from the CAS session (no user interaction)."""
        verifier, challenge = _pkce()
        _, _, callback = await self._browser.go(_authorize_url(challenge, silent=True))
        if callback is None:
            raise SessionExpired("login page returned")
        self._token = await _exchange_code(self._session, callback, verifier)

    async def _ensure_token(self) -> str:
        if self._token is None or self._token["expires_at"] - time.time() < 120:
            await self.async_refresh_token()
        if self._token is None:
            raise SessionExpired("no token")
        return self._token["access_token"]

    async def _get(self, path: str, retry: bool = True) -> Any:
        access_token = await self._ensure_token()
        try:
            async with self._session.get(
                API_URL + path,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                },
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status == 401 and retry:
                    self._token = None
                    return await self._get(path, retry=False)
                if resp.status != 200:
                    raise CannotConnect(f"{path}: HTTP {resp.status}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(str(err)) from err

    async def async_get_mobile_contracts(self) -> list[dict[str, Any]]:
        """Active mobile contracts of the customer."""
        await self._ensure_token()
        data = await self._get(f"/personnes/{self.person_id}/contrats-signes")
        return [
            c
            for c in data.get("items", [])
            if c.get("typeLigne") == "MOBILE" and c.get("statut") == "ACTIF"
        ]

    async def async_get_consumption(self, contract_id: str) -> dict[str, Any]:
        """Current billing-period consumption of a mobile contract."""
        return await self._get(f"/contrats/{contract_id}/suivi-conso-mobile")
