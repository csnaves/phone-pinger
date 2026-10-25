# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A FastAPI web service that authenticates with Apple iCloud and remotely triggers a sound on a specific device (matched by UDID from the `ICLOUD_DEVICE_ID` env var). It handles iCloud two-factor authentication via a simple web UI.

## Running the service

On the NUC this runs as the NSSM Windows service `LocatorService` (auto-starts on boot; see docs/setup.md). After code changes, restart it from an elevated shell: `net stop LocatorService && net start LocatorService`. Don't run it manually while the service is up — both bind port 8000.

For manual runs elsewhere (or with the service stopped):

```powershell
# Set required environment variables before starting
$env:ICLOUD_EMAIL = "your@email.com"
$env:ICLOUD_PASSWORD = "yourpassword"
$env:ICLOUD_DEVICE_ID = "<target-device-id>"
$env:LOCATOR_TOKEN = "<random-token>"   # python -c "import secrets; print(secrets.token_urlsafe(32))"

# Start the server
uvicorn phone_pinger.main:app --host 0.0.0.0 --port 8000
```

## Code layout

| Path | What's there |
|------|--------------|
| `phone_pinger/main.py` | App entry point (`phone_pinger.main:app`): router wiring, request-logging middleware, startup hook that logs in to iCloud |
| `phone_pinger/config.py` | Every env var and constant. Required vars fail at import with a clear message. `COOKIE_DIR` is anchored to the repo root |
| `phone_pinger/icloud.py` | iCloud session state (`api`, `device`, ...) behind `state_lock`, plus init / send_code / play_sound. Read it as `icloud.api`, never `from .icloud import api` (stale copy) |
| `phone_pinger/auth.py` | Token dependencies: `require_token`, `require_url_token` |
| `phone_pinger/routes/ui.py` | `/icloud` page routes |
| `phone_pinger/routes/ping.py` | `/ping` (POST, and token-in-URL GET for the Shelly button) |
| `phone_pinger/pages.py` | All HTML |
| `scripts/` | `dev.ps1` (dev instance), `find_devices.py` (list device IDs) |
| `docs/setup.md` | NUC service setup, Tailscale, daily use, gotchas |

Importing the app has no side effects. The iCloud login happens in the startup hook.

## Dev instance

`scripts/dev.ps1` runs a second instance against a separate iCloud account on `127.0.0.1:8001` with `--reload`. It reads its settings from the gitignored `dev.env` (template: `dev.env.example`) and refuses to start if a value is missing, a placeholder, or matches the machine-wide production value. The production service doesn't need to be stopped. Run it with `powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1`. To find a device ID for `dev.env`, run `scripts/find_devices.py`. Sessions for both accounts share `icloud_cookies/` (one file pair per email), which is fine as long as no two processes use the same account at once.

## API endpoints

Every endpoint requires the `LOCATOR_TOKEN` secret (403 if missing or wrong), sent as `Authorization: Bearer <token>`, `?token=<token>`, or the `locator_token` cookie that `GET /icloud?token=...` sets. FastAPI's `/docs`, `/redoc` and `/openapi.json` are disabled. HTML lives in `phone_pinger/pages.py`.

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/icloud` | HTML form to enter 2FA code. `?token=` swaps the token for a cookie and redirects |
| `POST` | `/icloud` | Submit 2FA code to complete auth |
| `POST` | `/icloud/resend` | Resend verification code to trusted device |
| `POST` | `/icloud/ping` | The page's "Ping phone" button: rings, then 303s to `/icloud?ping=sent\|failed` for the status line |
| `POST` | `/ping` | Play sound on the target device (401 if iCloud needs re-auth) |
| `GET`  | `/ping` | Same, for the Shelly button (GET-only). Requires `?token=`; the cookie is not accepted so links and prefetches can't ring the phone |

## Auth flow

On first run, pyicloud will need 2FA. The `/icloud` endpoints handle this interactively. After successful auth, the session is persisted to `icloud_cookies/` (keyed by email) so subsequent restarts skip 2FA.

## Dependencies

```
pip install -r requirements.txt
```

Key packages (pinned in `requirements.txt`): `pyicloud==2.5.0`, `fastapi==0.136.1`, `uvicorn==0.46.0`.

## Device targeting

The target device is matched by its stable UDID, read from the `ICLOUD_DEVICE_ID` env var into `config.DEVICE_ID` in `phone_pinger/config.py`. On the NUC it's set both machine-wide and on the service via NSSM `AppEnvironmentExtra`. This repo is meant to be shareable: keep names, device IDs, tailnet names, and LAN IPs out of tracked files. The UDID is immune to iCloud list reordering or renames; it only changes if the phone is wiped and re-added to the account. To find a device's UDID, iterate `api.devices` and read `device._content["id"]`.
