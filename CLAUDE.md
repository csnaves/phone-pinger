# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A FastAPI web service that authenticates with Apple iCloud and remotely triggers a sound on a specific device (matched by UDID from the `ICLOUD_DEVICE_ID` env var). It handles iCloud two-factor authentication via a simple web UI.

## Running the service

On the NUC this runs as the NSSM Windows service `LocatorService` (auto-starts on boot; see SETUP.md). After code changes, restart it from an elevated shell: `net stop LocatorService && net start LocatorService`. Don't run it manually while the service is up — both bind port 8000.

For manual runs elsewhere (or with the service stopped):

```powershell
# Set required environment variables before starting
$env:ICLOUD_EMAIL = "your@email.com"
$env:ICLOUD_PASSWORD = "yourpassword"
$env:ICLOUD_DEVICE_ID = "<target-device-id>"

# Start the server
uvicorn locator:app --host 0.0.0.0 --port 8000
```

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/icloud` | HTML form to enter 2FA code |
| `POST` | `/icloud` | Submit 2FA code to complete auth |
| `POST` | `/icloud/resend` | Resend verification code to trusted device |
| `GET`  | `/ping` | Play sound on the target device (requires prior auth) |

## Auth flow

On first run, pyicloud will need 2FA. The `/icloud` endpoints handle this interactively. After successful auth, the session is persisted to `icloud_cookies/` (keyed by email) so subsequent restarts skip 2FA.

## Dependencies

```
pip install -r requirements.txt
```

Key packages: `pyicloud==2.3.0`, `fastapi`, `uvicorn`.

## Device targeting

The target device is matched by its stable UDID, read from the `ICLOUD_DEVICE_ID` env var into `DEVICE_ID` in `locator.py`. On the NUC it's set both machine-wide and on the service via NSSM `AppEnvironmentExtra`. This repo is meant to be shareable: keep names, device IDs, tailnet names, and LAN IPs out of tracked files. The UDID is immune to iCloud list reordering or renames; it only changes if the phone is wiped and re-added to the account. To find a device's UDID, iterate `api.devices` and read `device._content["id"]`.
