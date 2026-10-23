# phone-pinger

A small FastAPI service that makes an iPhone play its Find My sound on demand. Hit one URL, the phone rings, even if it's on silent.

It runs as an always-on Windows service on a home server and is reached from anywhere over [Tailscale](https://tailscale.com).

## How it works

- Logs in to iCloud with [pyicloud](https://github.com/timlaing/pyicloud) using credentials from environment variables.
- Picks the target phone by its stable iCloud device ID (the `ICLOUD_DEVICE_ID` environment variable), so it keeps working if the account's device list is reordered or the phone is renamed.
- Saves the Apple session to `icloud_cookies/`, so restarts don't need a new 2FA code until Apple expires the session.
- If iCloud isn't connected yet (for example, the network wasn't up at boot), `/ping` retries the login itself.

## Endpoints

Every endpoint requires the secret in the `LOCATOR_TOKEN` environment variable, sent either as an `Authorization: Bearer <token>` header or as `?token=<token>` in the URL. A missing or wrong token gets a 403.

| Method | Path | What it does |
|--------|------|--------------|
| `POST` | `/ping` | Plays the sound on the target phone. Returns 401 if Apple needs re-authentication. |
| `GET`  | `/ping?token=...` | Same as `POST /ping`, for the Shelly button, which can only send GET. The token must be in the URL. |
| `GET`  | `/icloud?token=...` | Web page for entering a 2FA code (sends a code if one is needed). Saves the token in a browser cookie, so the page's buttons work and the token drops out of the URL. |
| `POST` | `/icloud` | Submits the 2FA code. |
| `POST` | `/icloud/resend` | Re-initializes the iCloud login and sends a fresh code. |
| `POST` | `/icloud/ping` | The page's **Ping phone** button. Rings the phone and returns to `/icloud` with a "Ping sent" status line. |

## Shelly button

A Shelly Wi-Fi button on the home network rings the phone with one press. No app, no Tailscale.

**How it's set up:** in the Shelly app (or the button's web UI), the short-press action is a URL action pointing at the NUC's LAN address:

```
http://<nuc-lan-ip>:8000/ping?token=<token>
```

When pressed, the button sends that HTTP GET straight to the NUC over the LAN, which is the same request as opening the URL in a browser. The NUC then calls Apple, and the phone rings.

**What it depends on:**
- **The NUC keeping `<nuc-lan-ip>`.** That address is set as a static IP on the NUC's Wi-Fi adapter. Make sure the router never hands `<nuc-lan-ip>` to another device: either reserve it for the NUC in the router's DHCP settings or keep it outside the DHCP pool. If the NUC's IP ever changes, update the URL in the Shelly app.
- **The service listening on all interfaces** (`--host 0.0.0.0`). Binding only to the Tailscale IP would lock the button out, because the button isn't on the tailnet.
- **`http`, not `https`.** Uvicorn serves plain HTTP.
- **A working iCloud session.** If Apple has expired the session, the button press gets a 401 and nothing rings. Re-authenticate at `http://<nuc-lan-ip>:8000/icloud?token=<token>`.
- **The current token.** If `LOCATOR_TOKEN` changes, update the URL in the Shelly app.

**If a press doesn't ring the phone:**
1. Open `http://<nuc-lan-ip>:8000/ping?token=<token>` in a browser on the home Wi-Fi. If that rings the phone, the problem is the button. If it doesn't, the problem is the service or iCloud.
2. Check `logs\locator.out.log` on the NUC for a `"GET /ping"` line from the button's IP at the time you pressed it. No line means the request never reached the NUC (check the button's Wi-Fi connection and the URL). A `403` means the token in the button's URL is wrong. A `401` means iCloud needs re-authentication.
3. Battery-powered Shelly buttons sleep between presses and take a few seconds to wake and join Wi-Fi, so a short delay is normal.

## Quick start (manual run)

```powershell
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt

$env:ICLOUD_EMAIL = "you@example.com"
$env:ICLOUD_PASSWORD = "your-apple-id-password"
$env:ICLOUD_DEVICE_ID = "<target-device-id>"
$env:LOCATOR_TOKEN = .\venv\Scripts\python -c "import secrets; print(secrets.token_urlsafe(32))"

.\venv\Scripts\python -m uvicorn phone_pinger.main:app --host 0.0.0.0 --port 8000
```

Then open `http://localhost:8000/icloud?token=<token>` to complete 2FA once, and press **Ping phone** on that page (or `curl -X POST -H "Authorization: Bearer <token>" http://localhost:8000/ping`) to ring the phone.

### Finding the device ID

List every device on an account with its ID. The script prompts for the Apple ID and password, and handles 2FA in the terminal if needed:

```powershell
.\venv\Scripts\python scripts\find_devices.py
```

Copy the ID of the phone you want into `ICLOUD_DEVICE_ID`. If two devices have the same name (an old phone still on the account, for example), pick the one that's online in Find My.

## Dev instance

`scripts/dev.ps1` runs a second copy against a different iCloud account, so you can test changes without ringing the production phone or stopping the service:

```powershell
copy dev.env.example dev.env      # then fill it in; dev.env is gitignored
powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1
```

It listens on `http://localhost:8001` (localhost only), reloads on code changes, and refuses to start if any value in `dev.env` is missing or matches production.

## Running it permanently

For running as a Windows service that starts on boot, reaching it remotely over Tailscale, and editing it over VS Code Remote-SSH, see [docs/setup.md](docs/setup.md). It also covers known gotchas and day-to-day use.

## Security notes

- `icloud_cookies/` holds a live Apple session that can access the account without a password or 2FA. It's gitignored. Never commit or share it.
- Every endpoint needs `LOCATOR_TOKEN`. Anyone who has the token and can reach port 8000 (your LAN or tailnet) can ring the phone, so treat the Shelly URL and any Shortcut that contains it as secrets.
- The token travels over plain HTTP. Tailscale encrypts it in transit; on the home LAN it's readable by anyone who can sniff the Wi-Fi.
- A `GET /ping` only rings with `?token=` in the URL. Link previews, browser history and prefetching can't set it off unless the shared URL includes the token.
- Credentials and the device ID come from environment variables only; nothing account-specific is stored in the code.
