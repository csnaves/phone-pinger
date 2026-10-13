# phone-pinger

A small FastAPI service that makes an iPhone play its Find My sound on demand. Hit one URL, the phone rings, even if it's on silent.

It runs as an always-on Windows service on a home server and is reached from anywhere over [Tailscale](https://tailscale.com).

## How it works

- Logs in to iCloud with [pyicloud](https://github.com/timlaing/pyicloud) using credentials from environment variables.
- Picks the target phone by its stable iCloud device ID (the `ICLOUD_DEVICE_ID` environment variable), so it keeps working if the account's device list is reordered or the phone is renamed.
- Saves the Apple session to `icloud_cookies/`, so restarts don't need a new 2FA code until Apple expires the session.
- If iCloud isn't connected yet (for example, the network wasn't up at boot), `/ping` retries the login itself.

## Endpoints

| Method | Path | What it does |
|--------|------|--------------|
| `GET`  | `/ping` | Plays the sound on the target phone. Returns 401 if Apple needs re-authentication. |
| `GET`  | `/icloud` | Web page for entering a 2FA code (sends a code if one is needed). |
| `POST` | `/icloud` | Submits the 2FA code. |
| `POST` | `/icloud/resend` | Re-initializes the iCloud login and sends a fresh code. |

## Shelly button

A Shelly Wi-Fi button on the home network rings the phone with one press. No app, no Tailscale.

**How it's set up:** in the Shelly app (or the button's web UI), the short-press action is a URL action pointing at the NUC's LAN address:

```
http://<nuc-lan-ip>:8000/ping
```

When pressed, the button sends that HTTP GET straight to the NUC over the LAN, which is the same request as opening the URL in a browser. The NUC then calls Apple, and the phone rings.

**What it depends on:**
- **The NUC keeping `<nuc-lan-ip>`.** That address is set as a static IP on the NUC's Wi-Fi adapter. Make sure the router never hands `<nuc-lan-ip>` to another device: either reserve it for the NUC in the router's DHCP settings or keep it outside the DHCP pool. If the NUC's IP ever changes, update the URL in the Shelly app.
- **The service listening on all interfaces** (`--host 0.0.0.0`). Binding only to the Tailscale IP would lock the button out, because the button isn't on the tailnet.
- **`http`, not `https`.** Uvicorn serves plain HTTP.
- **A working iCloud session.** If Apple has expired the session, the button press gets a 401 and nothing rings. Re-authenticate at `http://<nuc-lan-ip>:8000/icloud`.

**If a press doesn't ring the phone:**
1. Open `http://<nuc-lan-ip>:8000/ping` in a browser on the home Wi-Fi. If that rings the phone, the problem is the button. If it doesn't, the problem is the service or iCloud.
2. Check `logs\locator.err.log` on the NUC for a `GET /ping` line from the button's IP at the time you pressed it. No line means the request never reached the NUC (check the button's Wi-Fi connection and the URL). A `401` means iCloud needs re-authentication.
3. Battery-powered Shelly buttons sleep between presses and take a few seconds to wake and join Wi-Fi, so a short delay is normal.

## Quick start (manual run)

```powershell
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt

$env:ICLOUD_EMAIL = "you@example.com"
$env:ICLOUD_PASSWORD = "your-apple-id-password"
$env:ICLOUD_DEVICE_ID = "<target-device-id>"

.\venv\Scripts\python -m uvicorn locator:app --host 0.0.0.0 --port 8000
```

Then open `http://localhost:8000/icloud` to complete 2FA once, and `http://localhost:8000/ping` to ring the phone.

### Finding the device ID

With `ICLOUD_EMAIL` and `ICLOUD_PASSWORD` set (and a saved session in `icloud_cookies/`), list every device on the account with its ID:

```powershell
.\venv\Scripts\python -c "import os; from pyicloud import PyiCloudService; api = PyiCloudService(os.environ['ICLOUD_EMAIL'], os.environ['ICLOUD_PASSWORD'], cookie_directory='icloud_cookies'); [print(d._content['deviceDisplayName'], '|', d._content['name'], '|', d._content['id']) for d in api.devices]"
```

Copy the ID of the phone you want into `ICLOUD_DEVICE_ID`.

## Running it permanently

For running as a Windows service that starts on boot, reaching it remotely over Tailscale, and editing it over VS Code Remote-SSH, see [SETUP.md](SETUP.md). It also covers known gotchas and day-to-day use.

## Security notes

- `icloud_cookies/` holds a live Apple session that can access the account without a password or 2FA. It's gitignored. Never commit or share it.
- The endpoints have no authentication. Anyone who can reach port 8000 (your LAN or tailnet) can ring the phone.
- Credentials and the device ID come from environment variables only; nothing account-specific is stored in the code.
