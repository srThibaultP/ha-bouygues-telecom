"""Tests of the API client against a fake Bouygues Telecom server."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

import pytest
from aiohttp import ClientSession, CookieJar, web
from aiohttp.test_utils import TestServer

from custom_components.bouygues_telecom import api
from custom_components.bouygues_telecom.const import REDIRECT_URI

from .conftest import PERSON_ID, make_id_token

CODE = "123456"
FORM_LOGIN = """<form method="post" id="fm1">
<input type="hidden" name="username" value=""/>
<input type="hidden" name="password" value=""/>
<input type="hidden" name="execution" value="{execution}"/>
<input type="hidden" name="_eventId" value="submit"/>
<input type="submit" name="submit" value=""/>
</form><script>window.LOGIN_CONFIG = {{ MireLogin: {{ errorLogin: {error} }} }}</script>"""
FORM_METHOD = """<form method="post" id="contactSelectionForm">
<input type="hidden" name="maskedValue" id="contact" value=""/>
<input type="submit" name="_eventId_submit" value=""/>
<input type="hidden" name="execution" value="method"/>
</form><script>window.LOGIN_CONFIG = { OtpMethod: { tel: '06 ** 01', email: '' } }</script>"""
FORM_OTP = """<form method="post" id="fm1">
<input type="hidden" name="token" id="codeOtp" value=""/>
<input type="submit" name="_eventId_submit" value=""/>
<input type="submit" name="_eventId_regenerate" value=""/>
<input type="hidden" name="execution" value="otp"/>
</form><script>window.LOGIN_CONFIG = { OtpInput: { isSMS: true } }</script>"""
FORM_FINAL = """<form method="post" id="fm1">
<input type="submit" name="_eventId_proceed" value=""/>
<input type="hidden" name="execution" value="final"/>
</form><script>window.LOGIN_CONFIG = { OtpFinal: {} }</script>"""


@dataclass
class FakeBouygues:
    """State and behavior switches of the fake server."""

    url: str = ""
    password: str = "secret"
    skip_method: bool = False
    after_login: str | None = None
    final_without_form: bool = False
    otp_page_after_send: bool = True
    token_status: int = 200
    api_401_once: set[str] = field(default_factory=set)
    with_invoice_link: bool = True
    regenerate_seen: bool = False
    sent_to: list[str] = field(default_factory=list)


def _html(body: str, status: int = 200) -> web.Response:
    return web.Response(text=body, status=status, content_type="text/html")


def _to_callback(with_code: bool = True) -> web.HTTPFound:
    return web.HTTPFound(f"{REDIRECT_URI}?{'code=abc&' if with_code else ''}state=x")


def make_app(fake: FakeBouygues) -> web.Application:
    """Build the fake CAS, OAuth and API endpoints."""

    async def authorize(request: web.Request) -> web.StreamResponse:
        if "CASTGC" in request.cookies:
            raise _to_callback()
        if request.query.get("prompt") == "none":
            raise _to_callback(with_code=False)
        raise web.HTTPFound("/cas/login")

    async def login_page(request: web.Request) -> web.Response:
        return _html(FORM_LOGIN.format(execution="login", error="false"))

    async def login_post(request: web.Request) -> web.StreamResponse:
        form = await request.post()
        step = form.get("execution")
        if step == "login":
            if form.get("password") != fake.password:
                return _html(FORM_LOGIN.format(execution="login", error="true"), 401)
            if fake.after_login is not None:
                return _html(fake.after_login)
            if "MFATRUSTED" in request.cookies:
                return _logged_in()
            return _html(FORM_OTP if fake.skip_method else FORM_METHOD)
        if step == "method":
            fake.sent_to.append(str(form.get("maskedValue")))
            return _html(FORM_OTP if fake.otp_page_after_send else "<p>oops</p>")
        if step == "otp":
            fake.regenerate_seen |= "_eventId_regenerate" in form
            if form.get("token") != CODE:
                return _html(FORM_OTP)
            if fake.final_without_form:
                return _html("<script>OtpFinal</script>")
            response = _html(FORM_FINAL)
            response.set_cookie("MFATRUSTED", "trusted", path="/")
            return response
        if step == "final" and "_eventId_proceed" in form:
            return _logged_in(meta_refresh=True)
        return _html("<p>unexpected</p>")

    def _logged_in(meta_refresh: bool = False) -> web.Response:
        if meta_refresh:
            response = _html(
                '<meta http-equiv="refresh" content="0;URL=/cb?a=1&amp;b=2">'
            )
        else:
            response = web.Response(status=302, headers={"Location": "/cb"})
        response.set_cookie("CASTGC", "tgc", path="/")
        return response

    async def oauth_callback(request: web.Request) -> web.StreamResponse:
        raise _to_callback()

    async def loop(request: web.Request) -> web.StreamResponse:
        raise web.HTTPFound("/loop")

    async def token(request: web.Request) -> web.Response:
        form = await request.post()
        assert form["code_verifier"]
        if fake.token_status != 200:
            return web.Response(status=fake.token_status)
        return web.json_response(
            {"access_token": "at", "expires_in": 3600, "id_token": make_id_token()}
        )

    async def api_get(request: web.Request) -> web.Response:
        path = "/" + request.match_info["path"]
        if request.query_string:
            path += "?" + request.query_string
        if path == "/boom":
            return web.Response(status=500)
        if request.headers.get("Authorization") != "Bearer at":
            return web.Response(status=401)
        if path in fake.api_401_once:
            fake.api_401_once.discard(path)
            return web.Response(status=401)
        if path == f"/personnes/{PERSON_ID}":
            links = {"self": {"href": path}, "flag": True}
            if fake.with_invoice_link:
                links["factures"] = {"href": f"{path}/factures?idCP=1"}
            return web.json_response({"_links": links})
        if path == f"/personnes/{PERSON_ID}/factures?idCP=1":
            return web.json_response(
                {
                    "comptesFacturation": [
                        {"factures": [{"dateFacturation": "2026-10-15"}]},
                        {"factures": [{"dateFacturation": "2026-11-15"}]},
                    ]
                }
            )
        if path == f"/personnes/{PERSON_ID}/contrats-signes":
            return web.json_response(
                {
                    "items": [
                        {"id": "1", "typeLigne": "MOBILE", "statut": "ACTIF"},
                        {"id": "2", "typeLigne": "FIXE", "statut": "ACTIF"},
                        {"id": "3", "typeLigne": "MOBILE", "statut": "RESILIE"},
                    ]
                }
            )
        return web.json_response({"path": path})

    app = web.Application()
    app.router.add_get("/authorize", authorize)
    app.router.add_get("/cas/login", login_page)
    app.router.add_post("/cas/login", login_post)
    app.router.add_get("/cb", oauth_callback)
    app.router.add_get("/loop", loop)
    app.router.add_post("/token", token)
    app.router.add_get("/api/{path:.*}", api_get)
    return app


@pytest.fixture
async def fake(
    socket_enabled: None, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[FakeBouygues]:
    """Run the fake server and point the client at it."""
    state = FakeBouygues()
    server = TestServer(make_app(state))
    await server.start_server()
    state.url = str(server.make_url(""))
    monkeypatch.setattr(api, "AUTHORIZE_URL", f"{state.url}/authorize")
    monkeypatch.setattr(api, "TOKEN_URL", f"{state.url}/token")
    monkeypatch.setattr(api, "API_URL", f"{state.url}/api")
    yield state
    await server.close()


@pytest.fixture
async def session() -> AsyncGenerator[ClientSession]:
    """A client session that keeps cookies of the 127.0.0.1 test server."""
    async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as client:
        yield client


async def test_full_login(fake: FakeBouygues, session: ClientSession) -> None:
    """Password, contact, code and trusted-device page lead to a token."""
    flow = api.LoginFlow(session)
    contacts = await flow.start("user", "secret")
    assert contacts == api.OtpContacts("06 ** 01", None)
    await flow.send_code("06 ** 01")
    assert fake.sent_to == ["06 ** 01"]
    with pytest.raises(api.InvalidCode):
        await flow.verify("000000")
    token = await flow.verify(CODE)
    assert token["access_token"] == "at"
    assert not fake.regenerate_seen

    client = api.BouyguesTelecomClient(session)
    await client.async_refresh_token()
    assert client.person_id == PERSON_ID


async def test_login_errors(fake: FakeBouygues, session: ClientSession) -> None:
    """Bad password and unexpected pages are reported."""
    with pytest.raises(api.InvalidAuth):
        await api.LoginFlow(session).start("user", "wrong")
    fake.after_login = "<form></form><script>window.LOGIN_CONFIG = {}</script>"
    with pytest.raises(api.CannotConnect, match="unexpected page"):
        await api.LoginFlow(session).start("user", "secret")


async def test_code_sent_without_choice(
    fake: FakeBouygues, session: ClientSession
) -> None:
    """CAS may send the code to the only contact right away."""
    fake.skip_method = True
    assert await api.LoginFlow(session).start("user", "secret") == api.OtpContacts(
        None, None
    )


async def test_trusted_device_and_existing_session(
    fake: FakeBouygues, session: ClientSession
) -> None:
    """A trusted device skips the code; a live session skips the password."""
    session.cookie_jar.update_cookies({"MFATRUSTED": "trusted"})
    flow = api.LoginFlow(session)
    assert await flow.start("user", "secret") is None
    assert flow.token is not None

    again = api.LoginFlow(session)
    assert await again.start("user", "secret") is None
    assert again.token is not None


async def test_flow_page_errors(fake: FakeBouygues, session: ClientSession) -> None:
    """Missing forms and pages are reported as connection errors."""
    flow = api.LoginFlow(session)
    with pytest.raises(api.CannotConnect, match="contact selection"):
        await flow.send_code("x")
    with pytest.raises(api.CannotConnect, match="code entry form"):
        await flow.verify("x")

    await flow.start("user", "secret")
    fake.otp_page_after_send = False
    with pytest.raises(api.CannotConnect, match="code entry page"):
        await flow.send_code("06 ** 01")


async def test_final_page_without_form(
    fake: FakeBouygues, session: ClientSession
) -> None:
    """A confirmation page without a form ends without a token."""
    fake.final_without_form = True
    fake.skip_method = True
    flow = api.LoginFlow(session)
    await flow.start("user", "secret")
    with pytest.raises(api.CannotConnect, match="no authorization code"):
        await flow.verify(CODE)


async def test_login_page_missing(
    fake: FakeBouygues, session: ClientSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another page instead of the login form is an error."""
    monkeypatch.setattr(api, "AUTHORIZE_URL", f"{fake.url}/api/nothing")
    with pytest.raises(api.CannotConnect, match="login page"):
        await api.LoginFlow(session).start("user", "secret")


async def test_http_errors(fake: FakeBouygues, session: ClientSession) -> None:
    """Server errors, refused connections and redirect loops."""
    browser = api._Browser(session)
    with pytest.raises(api.CannotConnect, match="HTTP 500"):
        await browser.go(f"{fake.url}/api/boom")
    with pytest.raises(api.CannotConnect):
        await browser.go("http://127.0.0.1:1/")
    with pytest.raises(api.CannotConnect, match="too many redirects"):
        await browser.go(f"{fake.url}/loop", referer="x")


async def test_token_endpoint_errors(
    fake: FakeBouygues, session: ClientSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Token exchange failures."""
    with pytest.raises(api.SessionExpired):
        await api._exchange_code(session, f"{REDIRECT_URI}?error=x", "v")
    fake.token_status = 500
    with pytest.raises(api.CannotConnect, match="token endpoint"):
        await api._exchange_code(session, f"{REDIRECT_URI}?code=c", "v")
    monkeypatch.setattr(api, "TOKEN_URL", "http://127.0.0.1:1/token")
    with pytest.raises(api.CannotConnect):
        await api._exchange_code(session, f"{REDIRECT_URI}?code=c", "v")


async def test_refresh_without_session(
    fake: FakeBouygues, session: ClientSession
) -> None:
    """Without a CAS session, renewal needs the password, then a code."""
    client = api.BouyguesTelecomClient(session)
    with pytest.raises(api.SessionExpired):
        _ = client.person_id
    with pytest.raises(api.SessionExpired):
        await client.async_refresh_token()

    with pytest.raises(api.CodeRequired):
        await api.BouyguesTelecomClient(session, "user", "secret").async_refresh_token()

    session.cookie_jar.update_cookies({"MFATRUSTED": "trusted"})
    client = api.BouyguesTelecomClient(session, "user", "secret")
    await client.async_refresh_token()
    assert client.person_id == PERSON_ID


async def test_api_calls(fake: FakeBouygues, session: ClientSession) -> None:
    """Contracts, consumption, extras, details and invoices."""
    session.cookie_jar.update_cookies({"CASTGC": "tgc"})
    client = api.BouyguesTelecomClient(session)
    assert [c["id"] for c in await client.async_get_mobile_contracts()] == ["1"]

    fake.api_401_once.add("/contrats/1/suivi-conso-mobile")
    assert await client.async_get_consumption("1") == {
        "path": "/contrats/1/suivi-conso-mobile"
    }
    extras = await client.async_get_contract_extras("1")
    assert extras["incidents"] == {"path": "/incidents-collectifs?idsContrats=1"}
    details = await client.async_get_contract_details("1")
    assert details["options"] == {"path": "/contrats/1/options"}
    invoices = await client.async_get_invoices()
    assert [i["dateFacturation"] for i in invoices] == ["2026-11-15", "2026-10-15"]

    fake.with_invoice_link = False
    assert await client.async_get_invoices() == []

    with pytest.raises(api.CannotConnect, match="HTTP 500"):
        await client._get("/boom")


async def test_api_connection_error(
    fake: FakeBouygues, session: ClientSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused API connection is a connection error."""
    session.cookie_jar.update_cookies({"CASTGC": "tgc"})
    client = api.BouyguesTelecomClient(session)
    await client.async_refresh_token()
    monkeypatch.setattr(api, "API_URL", "http://127.0.0.1:1")
    with pytest.raises(api.CannotConnect):
        await client._get("/x")


@pytest.mark.usefixtures("socket_enabled")
async def test_cookie_round_trip() -> None:
    """Only Bouygues cookies are exported, and they load back."""
    async with ClientSession() as first, ClientSession() as second:
        cookies = [
            {
                "name": "CASTGC",
                "value": "tgc",
                "domain": "www.mon-compte.bouyguestelecom.fr",
                "path": "/cas",
            }
        ]
        api.import_cookies(first, cookies)
        api.import_cookies(
            first,
            [{"name": "x", "value": "1", "domain": "example.com", "path": "/"}],
        )
        exported = api.export_cookies(first)
        assert exported == cookies
        api.import_cookies(second, exported)
        assert api.export_cookies(second) == cookies


def test_form_helpers() -> None:
    """Forms are parsed and only forward events are submitted."""
    assert api._first_form("<p>nothing</p>") is None
    form = api._first_form(FORM_OTP)
    assert form is not None
    assert api._form_fields(form) == {
        "token": "",
        "_eventId_submit": "",
        "execution": "otp",
    }
