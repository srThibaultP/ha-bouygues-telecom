<p align="center"><img src="custom_components/bouygues_telecom/brand/logo.png" alt="Bouygues Telecom" height="64"></p>

# Bouygues Telecom Mobile for Home Assistant

Custom integration that brings the **mobile plan consumption, invoices and line status** of a Bouygues Telecom consumer account into Home Assistant, using the same API as the web customer area.

> Unofficial. Not affiliated with or endorsed by Bouygues Telecom. The logo is a trademark of Bouygues Telecom and is only used to identify the service. The API is undocumented and may change without notice.

## Use cases

- Get a notification at 80 % of your data allowance, or when out-of-plan charges appear.
- Be warned when Bouygues Telecom reports a network incident on your lines.
- Track the monthly data usage history (Home Assistant long-term statistics) and the CO₂ estimate.
- See the next invoice amount and due date, and when your phone becomes eligible for renewal.

## Supported accounts

Consumer (B&You / Bouygues Telecom) accounts that can sign in to [the customer area](https://www.bouyguestelecom.fr/mon-compte). Every active **mobile** line of the account becomes a device; lines that share data (multi-SIM options) report the shared counter. Fixed lines (Bbox) and Bouygues Telecom Entreprises accounts are not supported.

## Entities

### Per mobile line

| Entity | Type | Notes |
| --- | --- | --- |
| Data used / allowance / remaining | sensor (GiB) | Main counter of the current billing period |
| Data used percentage | sensor (%) | |
| Europe/DOM data used / allowance | sensor (GiB) | Roaming sub-allowance, disabled by default |
| Calls used | sensor (min) | |
| Calls allowance | sensor (min) | Only on plans with limited calls |
| Out-of-plan charges | sensor (EUR) | Attribute `details`: the out-of-plan items |
| Data top-ups bought | sensor | Attribute `top_ups`: the bought top-ups |
| Carbon footprint | sensor (kg) | CO₂ estimate of the period, from Bouygues Telecom |
| Plan price | sensor (EUR/month) | Discounted price; attributes: plan, price without discount, discount. Only on lines billed separately |
| Subscribed options | sensor (diagnostic) | Attribute `options`: names of the options |
| Phone renewal eligibility date | sensor (date) | When the line has a renewal date |
| Renewal date | sensor (date) | Start of the next billing period |
| Last reading | sensor (diagnostic) | When Bouygues Telecom last updated the counters |
| Network incident | binary sensor (problem) | On while a collective incident affects the line |
| Out-of-plan data threshold reached | binary sensor (problem) | Attributes per zone (France/Europe, rest of the world) |
| Phone renewal eligible | binary sensor | |

### Account

| Entity | Type |
| --- | --- |
| Last invoice amount | sensor (EUR) |
| Last invoice date | sensor (date) |
| Invoice due date | sensor (date) |
| Balance | sensor (EUR) |

## Installation

### HACS

1. HACS → ⋮ → **Custom repositories** → add this repository as an *Integration*.
2. Install **Bouygues Telecom Mobile** and restart Home Assistant.

### Manual

Copy `custom_components/bouygues_telecom` into the `config/custom_components/` folder of Home Assistant and restart.

## Configuration

**Settings → Devices & services → Add integration → Bouygues Telecom Mobile**, then:

1. **Login and password** of the customer area (e-mail or line number).
2. **Where to send the one-time code** (SMS or e-mail), when Bouygues Telecom asks for one.
3. **The code** you received.

Use **Reconfigure** on the integration to change the login or password.

## How data is updated

- Consumption, out-of-plan details, top-ups and incidents: every **30 minutes**.
- Plan price, options, phone renewal and invoices: every **6 hours** (and when a line is added).
- Bouygues Telecom itself refreshes the counters a few times a day; the *Last reading* sensor tells when.
- If a line's endpoint briefly fails (the API sometimes answers HTTP 503 for a few minutes), that line keeps its last reading until the next poll instead of making every entity unavailable.

### Authentication

The customer area uses a CAS login with a one-time code behind an OAuth2 PKCE flow, and issues no refresh token. The integration:

- keeps the CAS "remember me" session cookies and mints a new one-hour access token silently whenever needed;
- after the first code, Bouygues Telecom trusts this Home Assistant for about a year, so when the session expires the integration signs in again with the stored password **without a new code**;
- only if a code is required again, or the password is rejected, does Home Assistant ask you to sign in again (repair notification).

The login, password and session cookies are stored in the config entry, like most cloud integrations.

## Known limitations

- Uses an undocumented API; a change on Bouygues Telecom's side can break it.
- No per-day history from Bouygues Telecom: use the statistics of *Data used* (e.g. a `statistics-graph` card with *change* per day).
- Too many failed code attempts make Bouygues Telecom stop sending codes for a while.

## Troubleshooting

- **"Signed in, but Bouygues Telecom refused to renew the session"**: the session could not mint a token without a code. Try again later; if it persists, open an issue with the diagnostics.
- **No code received**: choose the other destination (e-mail/SMS), check spam, and wait a little after several attempts.
- **Re-authentication requested**: Bouygues Telecom asked for a new code (e.g. the trusted-device period ended or the password changed). Follow the notification.
- Download the **diagnostics** from the integration page (personal data is redacted) when reporting an issue.

## Removal

Settings → Devices & services → Bouygues Telecom Mobile → ⋮ → **Delete**, then remove the folder (or uninstall it from HACS) and restart.

## Development

```bash
# from a Home Assistant core checkout with `script/setup` done
uv run --no-sync pytest --cov        # 100 % coverage required
MYPYPATH=<ha-core> uv run --no-sync mypy
```

`tools/cli.py` exercises the API client against the real service (needs `aiohttp`); `tools/bt_probe.py` is the stdlib-only probe used to reverse-engineer the login. Both keep their state in `tools/state/` (git-ignored).
