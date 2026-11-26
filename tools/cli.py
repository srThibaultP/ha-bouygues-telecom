#!/usr/bin/env python3
"""Exercise the integration's API client outside Home Assistant.

  cli.py login   interactive login (password + one-time code), saves the session
  cli.py renew   mint a token silently from the saved session
  cli.py conso   renew + print the consumption of every mobile line

Needs aiohttp (e.g. the Home Assistant dev venv). The session is kept in
tools/state/session.json (git-ignored).
"""

from __future__ import annotations

import asyncio
import getpass
import importlib.util
import json
import os
import sys
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
STATE = Path(__file__).resolve().parent / "state"
SESSION_FILE = STATE / "session.json"


def _load_api():
    pkg = ROOT / "custom_components" / "bouygues_telecom"
    for name in ("const", "api"):
        spec = importlib.util.spec_from_file_location(
            f"bt.{name}", pkg / f"{name}.py", submodule_search_locations=[]
        )
        module = importlib.util.module_from_spec(spec)
        module.__package__ = "bt"
        sys.modules[f"bt.{name}"] = module
        spec.loader.exec_module(module)
    return sys.modules["bt.api"]


api = _load_api()


def _save(session: aiohttp.ClientSession) -> None:
    STATE.mkdir(exist_ok=True)
    SESSION_FILE.write_text(json.dumps(api.export_cookies(session)))
    os.chmod(SESSION_FILE, 0o600)


async def login() -> None:
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as session:
        flow = api.LoginFlow(session)
        username = input("Identifiant : ").strip()
        contacts = await flow.start(username, getpass.getpass("Mot de passe : "))
        if contacts is not None:
            if contacts.phone or contacts.email:
                choice = input(
                    f"Code par SMS ({contacts.phone}) ou e-mail ({contacts.email}) ? [s/e] : "
                ).strip().lower()
                contact = contacts.email if choice.startswith("e") else contacts.phone
                await flow.send_code(contact)
                print(f"code envoyé à {contact}")
            while True:
                try:
                    await flow.verify(input("Code reçu : "))
                    break
                except api.InvalidCode:
                    print("code refusé, réessaie")
        _save(session)
        print("connecté, session enregistrée; test du renouvellement silencieux…")
        client = api.BouyguesTelecomClient(session)
        await client.async_refresh_token()
        _save(session)
        print("renouvellement silencieux OK")


async def conso(print_lines: bool = True) -> None:
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as session:
        api.import_cookies(session, json.loads(SESSION_FILE.read_text()))
        client = api.BouyguesTelecomClient(session)
        await client.async_refresh_token()
        print("renouvellement silencieux OK")
        if print_lines:
            for c in await client.async_get_mobile_contracts():
                s = await client.async_get_consumption(str(c["id"]))
                name = (c.get("personnalisationContrat") or {}).get("nom") or c["id"]
                print(f"\n{name} — {c['abonnement']['libelle']} (relevé {s.get('dateReleve')})")
                for k in s.get("compteursConsoDATA", []):
                    print(f"  data {k['type']:<13} {k['quantiteConsommee'] / 2**30:7.2f} / "
                          f"{k['quantiteTotale'] / 2**30:7.2f} Gio")
        _save(session)


if __name__ == "__main__":
    command = (sys.argv[1:] or [""])[0]
    if command == "login":
        asyncio.run(login())
    elif command in ("renew", "conso"):
        asyncio.run(conso(print_lines=command == "conso"))
    else:
        sys.exit(__doc__)
