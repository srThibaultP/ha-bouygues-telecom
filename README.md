# Bouygues Telecom Mobile for Home Assistant

Custom integration that reads the **mobile plan consumption** (data, calls, out-of-plan charges) of every mobile line of a Bouygues Telecom consumer account, using the same API as the web customer area.

> Unofficial. Not affiliated with Bouygues Telecom. The API is undocumented and may change without notice.

## Sensors (per mobile line)

| Sensor | Notes |
| --- | --- |
| Data used | Main counter of the current billing period (resets on the renewal date) |
| Data allowance | Size of the main data counter |
| Data remaining | Allowance minus usage |
| Data used percentage | |
| Europe/DOM data used / allowance | Roaming sub-allowance, disabled by default |
| Calls used | |
| Calls allowance | Only on plans with limited calls |
| Out-of-plan charges | EUR, incl. VAT |
| Renewal date | Start of the next billing period |
| Last reading | When Bouygues Telecom last updated the counters |

Lines that share data (e.g. a multi-SIM option) report the shared counter.
Data is polled every 30 minutes.

## Installation

1. Copy `custom_components/bouygues_telecom` into your Home Assistant `config/custom_components/` folder (or add this repository as a custom repository in HACS).
2. Restart Home Assistant.
3. **Settings → Devices & services → Add integration → Bouygues Telecom Mobile**.
4. Enter your customer-area login and password, choose SMS or e-mail, and type the one-time code you receive.

## How authentication works

The customer area uses a CAS login with a one-time code, behind an OAuth2 PKCE flow, and does not issue refresh tokens. The integration therefore:

- keeps the CAS "remember me" session cookies and mints a new one-hour access token silently (`prompt=none`) whenever needed;
- after the first successful code, Bouygues Telecom marks the client as a **trusted device** for one year (`MFATRUSTED` cookie), so when the session expires the integration signs in again with the stored password **without a new code**;
- only if Bouygues Telecom asks for a code again (or the password changes) does Home Assistant show a re-authentication request.

The login, password and session cookies are stored in the config entry, like most cloud integrations.

## Development

`tools/cli.py` exercises the API client outside Home Assistant (needs `aiohttp`):

```bash
python tools/cli.py login   # interactive login, saves the session in tools/state/ (git-ignored)
python tools/cli.py conso   # silent renewal + consumption of every line
```

`tools/bt_probe.py` is the stdlib-only probe used to reverse-engineer the login flow; it dumps the CAS pages into `tools/state/debug/`.
