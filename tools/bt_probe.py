#!/usr/bin/env python3
"""Probe the Bouygues Telecom customer-area auth + consumption API (stdlib only).

Usage:
  bt_probe.py login     # interactive: email, password (hidden), SMS code
  bt_probe.py renew     # silent token renewal from the saved CAS session
  bt_probe.py conso     # renew + print the mobile consumption of every line

The CAS session cookies are stored in ./state/ (git-ignored). Nothing secret is
printed; HTML pages are dumped to ./state/debug/ for inspection.
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import http.cookiejar
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

CLIENT_ID = "a360.bouyguestelecom.fr"
REDIRECT_URI = "https://www.bouyguestelecom.fr/mon-compte/all//callback.html"
AUTHORIZE = "https://oauth2.bouyguestelecom.fr/authorize"
TOKEN = "https://oauth2.bouyguestelecom.fr/token"
API = "https://api.bouyguestelecom.fr"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Safari/605.1.15"
)

STATE = Path(__file__).resolve().parent / "state"
DEBUG = STATE / "debug"
JAR_FILE = STATE / "cookies.txt"
TOKEN_FILE = STATE / "token.json"


class FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict] = []
        self._cur: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self._cur = {"action": a.get("action"), "id": a.get("id"), "inputs": []}
            self.forms.append(self._cur)
        elif tag in ("input", "button", "select", "textarea") and self._cur is not None:
            self._cur["inputs"].append(a)

    def handle_endtag(self, tag):
        if tag == "form":
            self._cur = None


class StopAtCallback(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but stop at the OAuth redirect_uri (we only need its query)."""

    def __init__(self) -> None:
        self.chain: list[str] = []
        self.callback_url: str | None = None

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.chain.append(newurl.split("?")[0])
        if newurl.startswith("https://www.bouyguestelecom.fr/mon-compte/all"):
            self.callback_url = newurl
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener(jar):
    handler = StopAtCallback()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), handler)
    op.addheaders = [("User-Agent", UA), ("Accept-Language", "fr-FR,fr;q=0.9")]
    return op, handler


def _jar():
    STATE.mkdir(exist_ok=True)
    DEBUG.mkdir(exist_ok=True)
    jar = http.cookiejar.MozillaCookieJar(str(JAR_FILE))
    if JAR_FILE.exists():
        jar.load(ignore_discard=True, ignore_expires=True)
    return jar


def _save(jar):
    jar.save(ignore_discard=True, ignore_expires=True)
    os.chmod(JAR_FILE, 0o600)


def _pkce():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _authorize_url(challenge, prompt=None):
    q = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": secrets.token_urlsafe(12),
        "nonce": secrets.token_urlsafe(12),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "tmpl": "sasUnique",
    }
    if prompt is not None:
        q["prompt"] = prompt
    return AUTHORIZE + "?" + urllib.parse.urlencode(q)


def _open(op, handler, url, data=None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    try:
        resp = op.open(url, data=body, timeout=30)
        return resp.status, resp.geturl(), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if handler.callback_url:  # redirect to callback stopped us
            return e.code, handler.callback_url, ""
        return e.code, url, e.read().decode("utf-8", "replace")


def _dump(name, html):
    (DEBUG / name).write_text(html)


def _forms(html):
    p = FormParser()
    p.feed(html)
    return p.forms


def _describe(forms):
    for f in forms:
        print(f"  form id={f['id']} action={f['action']}")
        for i in f["inputs"]:
            v = i.get("value") or ""
            v = (v[:20] + "...") if len(v) > 20 else v
            print(f"    <{i.get('type','?')}> name={i.get('name')} id={i.get('id')} value={v!r}")


def _exchange(code, verifier):
    data = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "code_verifier": verifier,
        }
    ).encode()
    req = urllib.request.Request(TOKEN, data=data, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            tok = json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"token exchange failed: HTTP {e.code} {e.read()[:300]!r}")
    tok["obtained_at"] = int(time.time())
    TOKEN_FILE.write_text(json.dumps(tok))
    os.chmod(TOKEN_FILE, 0o600)
    print("token OK: keys", sorted(tok), "expires_in", tok.get("expires_in"),
          "refresh_token" in tok and "(refresh token present)" or "(no refresh token)")
    return tok


def _code_from(url):
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    return q.get("code", [None])[0], q.get("error", [None])[0]


def cmd_login():
    jar = _jar()
    op, h = _opener(jar)
    verifier, challenge = _pkce()
    status, url, html = _open(op, h, _authorize_url(challenge))
    _dump("1-login.html", html)
    forms = _forms(html)
    fm = next((f for f in forms if f["id"] == "fm1"), None)
    if not fm:
        _describe(forms)
        sys.exit(f"no login form at {url.split('?')[0]} (HTTP {status})")
    data = {i["name"]: i.get("value") or "" for i in fm["inputs"] if i.get("name")}
    data["username"] = input("Identifiant (e-mail ou n°) : ").strip()
    data["password"] = getpass.getpass("Mot de passe : ")
    data["rememberMe"] = "true"
    post_url = urllib.parse.urljoin(url, fm["action"] or url)
    data["submit"] = ""
    op.addheaders += [("Origin", "https://www.mon-compte.bouyguestelecom.fr"), ("Referer", url)]
    status, url, html = _open(op, h, post_url, data)
    data["password"] = ""
    _dump("2-after-password.html", html)
    print(f"after password: HTTP {status} {url.split('?')[0]}")
    if "errorLogin: true" in html:
        sys.exit("identifiant ou mot de passe refusé (errorLogin) — relance pour réessayer")

    for step in range(4):  # contact choice page, OTP page, maybe a final page
        if h.callback_url:
            break
        forms = _forms(html)
        _describe(forms)
        fm = forms[0] if forms else None
        if not fm:
            sys.exit(f"no form at step {step} — see state/debug/")
        # submit buttons: send only the "submit" event, never e.g. _eventId_regenerate
        fields = {i["name"]: i.get("value") or "" for i in fm["inputs"]
                  if i.get("name") and (i.get("type") != "submit" or i["name"] == "_eventId_submit")}
        id2name = {i.get("id"): i.get("name") for i in fm["inputs"] if i.get("id")}
        # field the page's JS callback fills in: getElementById("x").value = ...
        cb_ids = re.findall(r'getElementById\(["\'](\w+)["\']\)\.value\s*=', html)
        target = next((id2name[i] for i in cb_ids if id2name.get(i)), None)
        m_tel = re.search(r"OtpMethod:\s*{\s*tel:\s*'([^']*)'", html)
        if m_tel:
            fields[target or "maskedValue"] = m_tel.group(1)
            print(f"→ envoi du code par SMS au {m_tel.group(1)}")
        elif "OtpFinal" in html and "OtpInput" not in html:
            pass  # confirmation page: just submit it
        else:
            name = target or "token"
            fields[name] = input(f"Code SMS (champ {name}) : ").strip()
        post_url = urllib.parse.urljoin(url, fm["action"] or url)
        op.addheaders = [hd for hd in op.addheaders if hd[0] != "Referer"] + [("Referer", url)]
        status, url, html = _open(op, h, post_url, fields)
        _dump(f"3-step-{step}.html", html)
        print(f"step {step}: HTTP {status} {url.split('?')[0]}")
        m_ref = re.search(r'http-equiv="refresh" content="0;URL=([^"]+)"', html)
        if m_ref and not h.callback_url:
            nxt = m_ref.group(1).replace("&amp;", "&")
            print("meta refresh →", nxt.split("?")[0], "authnError" in nxt and "(authnError!)" or "")
            status, url, html = _open(op, h, nxt)

    _save(jar)
    print("redirect chain:", h.chain)
    code, err = _code_from(h.callback_url or "")
    if not code:
        sys.exit(f"no authorization code (error={err}); see state/debug/")
    _exchange(code, verifier)
    print("cookies kept:", sorted({(c.domain, c.name, bool(c.expires)) for c in jar}))


def cmd_renew(quiet=False):
    jar = _jar()
    op, h = _opener(jar)
    verifier, challenge = _pkce()
    status, url, html = _open(op, h, _authorize_url(challenge, prompt="none"))
    _save(jar)
    code, err = _code_from(h.callback_url or "")
    if not code:
        _dump("renew-fail.html", html)
        sys.exit(f"silent renew failed: error={err} last={url.split('?')[0]} chain={h.chain}")
    return _exchange(code, verifier)


def _api(tok, path):
    req = urllib.request.Request(API + path, headers={
        "Authorization": f"Bearer {tok['access_token']}", "User-Agent": UA,
        "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _personne(tok):
    idt = tok.get("id_token", "")
    payload = idt.split(".")[1] + "=="
    return json.loads(base64.urlsafe_b64decode(payload))["id_personne"]


def cmd_conso():
    tok = cmd_renew()
    pid = _personne(tok)
    contrats = _api(tok, f"/personnes/{pid}/contrats-signes").get("items", [])
    for c in contrats:
        if c.get("typeLigne") != "MOBILE":
            continue
        s = _api(tok, f"/contrats/{c['id']}/suivi-conso-mobile")
        print(f"\ncontrat {c['id']} ({c.get('statut')}) relevé {s.get('dateReleve')} "
              f"période {s.get('dateDebutConso')} → {s.get('dateReinitialisation')}")
        for k in s.get("compteursConsoDATA", []):
            print(f"  DATA {k['type']:<12} {k['quantiteConsommee']/1e9:8.2f} / "
                  f"{k['quantiteTotale']/1e9:8.2f} Go  {k.get('libelleCompteur','')}")
        for k in s.get("compteursConsoVOIX", []):
            print(f"  VOIX {k.get('libelleCompteur','')}: {k['quantiteConsommee']}s / {k['quantiteTotale']}s")
        print(f"  hors forfait: {s.get('horsForfait', {}).get('montantTotalHorsForfaitTTC')} €")


if __name__ == "__main__":
    {"login": cmd_login, "renew": cmd_renew, "conso": cmd_conso}.get(
        (sys.argv[1:] or ["?"])[0], lambda: sys.exit(__doc__))()
