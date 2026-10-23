import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from pyicloud import PyiCloudService
from pyicloud.exceptions import PyiCloudAPIResponseException, PyiCloudFailedLoginException
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
from datetime import datetime, timedelta
import logging
import os
import threading
import time

# ----------------------
# Logging
# ----------------------
LOG_FORMAT = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")

log = logging.getLogger("locator")
log.setLevel(logging.INFO)
log.propagate = False
_log_handler = logging.StreamHandler(sys.stdout)
_log_handler.setFormatter(LOG_FORMAT)
log.addHandler(_log_handler)

# The uvicorn CLI configures its loggers before importing this module, so restamp
# its startup/shutdown/error output and drop its access log in favor of log_requests.
for _h in logging.getLogger("uvicorn").handlers:
    _h.setFormatter(LOG_FORMAT)
logging.getLogger("uvicorn.access").disabled = True

# ----------------------
# Configuration
# ----------------------
COOKIE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icloud_cookies")
os.makedirs(COOKIE_DIR, exist_ok=True)

# Stable iCloud device id of the target phone. Immune to iCloud reordering/renames;
# only changes if the phone is wiped and re-added to the account.
DEVICE_ID = os.environ["ICLOUD_DEVICE_ID"]
MIN_RESEND_INTERVAL = timedelta(seconds=30)

log.info(f"Using cookie dir: {COOKIE_DIR}")

# ----------------------
# Module state (mutate only while holding state_lock)
# ----------------------
api: "PyiCloudService | None" = None
device = None
last_error: "str | None" = None
code_sent_at: "datetime | None" = None
state_lock = threading.Lock()


def _refresh_device() -> None:
    global device
    if api is None:
        device = None
        return
    try:
        match = next((d for d in api.devices if d._content.get("id") == DEVICE_ID), None)
        if match is None:
            log.warning(f"⚠️ No device with id {DEVICE_ID[:12]}... in account")
            device = None
            return
        device = match
        name = match._content.get("name", "?")
        model = match._content.get("deviceDisplayName", "?")
        log.info(f"🎯 Target device: {name} ({model})")
    except Exception as e:
        log.warning(f"⚠️ Could not resolve device: {e}")
        device = None


def init_api() -> None:
    """Build a fresh PyiCloudService. Caller must hold state_lock."""
    global api, device, last_error, code_sent_at
    try:
        api = PyiCloudService(
            os.environ["ICLOUD_EMAIL"],
            os.environ["ICLOUD_PASSWORD"],
            cookie_directory=COOKIE_DIR,
            accept_terms=True,
        )
        last_error = None
        code_sent_at = None
        _refresh_device()
        log.info(f"✅ iCloud client initialized (requires_2fa={api.requires_2fa})")
    except Exception as e:
        api = None
        device = None
        last_error = f"{type(e).__name__}: {e}"
        log.warning(f"⚠️ iCloud init failed: {last_error}")


def send_code(force: bool = False) -> "tuple[bool, str]":
    """Push a 2FA code to trusted devices. Caller must hold state_lock."""
    global code_sent_at
    if api is None:
        return False, last_error or "iCloud client not initialized."
    if not api.requires_2fa:
        return True, "Session already trusted; no code needed."

    if not force and code_sent_at is not None:
        elapsed = datetime.now() - code_sent_at
        if elapsed < MIN_RESEND_INTERVAL:
            wait = int((MIN_RESEND_INTERVAL - elapsed).total_seconds())
            return True, f"Code sent {int(elapsed.total_seconds())}s ago. Wait {wait}s before resend."

    try:
        ok = api.request_2fa_code()
    except Exception as e:
        return False, f"Failed to request code: {e}"
    if not ok:
        return False, "Apple declined to send a code (security key may be required)."

    code_sent_at = datetime.now()
    method = getattr(api, "two_factor_delivery_method", "trusted_device")
    log.info(f"📲 2FA code sent via {method}")
    return True, f"Code sent via {method}."


# Best-effort startup — never crash the server here.
with state_lock:
    init_api()
    send_code()


# ----------------------
# FastAPI app
# ----------------------
app = FastAPI()


@app.middleware("http")
async def log_requests(request: Request, call_next):
    # Logged on completion even if the client hung up, unlike uvicorn's access log.
    # Path only (no query string) so future tokens don't land in the log.
    start = time.perf_counter()
    client = request.client.host if request.client else "?"
    ua = request.headers.get("user-agent", "-")
    try:
        response = await call_next(request)
    except Exception:
        log.exception(f'{client} "{request.method} {request.url.path}" failed ua="{ua}"')
        raise
    ms = (time.perf_counter() - start) * 1000
    log.info(f'{client} "{request.method} {request.url.path}" {response.status_code} {ms:.0f}ms ua="{ua}"')
    return response


def render_page(content: str, status_msg: str = "") -> str:
    if api is None:
        banner_text = "⚠️ Not Connected"
        banner_color = "#F44336"
    elif device is None or api.requires_2fa:
        banner_text = "⚠️ MFA Required"
        banner_color = "#FF9800"
    else:
        banner_text = "✅ Authenticated"
        banner_color = "#4CAF50"

    status_html = f'<div class="status">{status_msg}</div>' if status_msg else ""

    return f"""
    <html>
      <head>
        <title>iCloud Control</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <style>
          body {{ font-family: system-ui; text-align: center; margin-top: 40px; background: #111; color: white; }}
          .banner {{ background-color: {banner_color}; padding: 12px; border-radius: 8px; margin-bottom: 16px; font-size: 18px; }}
          .status {{ background: #222; padding: 10px; border-radius: 6px; margin-bottom: 20px; color: #ccc; font-size: 14px; }}
          input {{ padding: 10px; font-size: 18px; text-align: center; border-radius: 6px; border: none; }}
          button {{ padding: 10px; font-size: 16px; border-radius: 6px; border: none; background: #4f8cff; color: white; margin-top: 12px; cursor: pointer; }}
          form {{ margin: 8px; }}
          a {{ color: #4f8cff; }}
        </style>
      </head>
      <body>
        <div class="banner">{banner_text}</div>
        {status_html}
        {content}
      </body>
    </html>
    """


def render_2fa_form(status_msg: str = "") -> str:
    return render_page("""
        <h2>iCloud Verification Required</h2>
        <p>A code should be on your trusted Apple devices.</p>
        <form method="post" action="/icloud">
          <input name="code" type="text" inputmode="numeric" maxlength="6" placeholder="6-digit code" required autofocus />
          <br>
          <button type="submit">Verify</button>
        </form>
        <form method="post" action="/icloud/resend">
          <button type="submit">Resend Code</button>
        </form>
    """, status_msg=status_msg)


# ----------------------
# /icloud — verification UI
# ----------------------
@app.get("/icloud", response_class=HTMLResponse)
def icloud_ui():
    with state_lock:
        if api is None or device is None:
            init_api()

        if api is None:
            return render_page(
                f"<h2>⚠️ Cannot connect to iCloud</h2><p>{last_error}</p>"
                "<form method='post' action='/icloud/resend'><button type='submit'>Try Again</button></form>"
            )

        if not api.requires_2fa and device is not None:
            return render_page("<h2>✅ No verification needed.</h2><p><a href='/ping'>Try /ping</a></p>")

        # 2FA required: auto-send a code (rate-limited so refreshes don't spam Apple)
        _, msg = send_code(force=False)
        return render_2fa_form(status_msg=msg)


@app.post("/icloud", response_class=HTMLResponse)
def submit_code(code: str = Form(...)):
    global code_sent_at
    with state_lock:
        if api is None:
            return render_page(f"<h2>⚠️ Not connected.</h2><p>{last_error}</p>")

        if not api.requires_2fa:
            _refresh_device()
            return render_page("<h2>ℹ️ No verification needed.</h2>")

        try:
            success = api.validate_2fa_code(code)
        except Exception as e:
            return render_page(f"<h2>❌ Verification error</h2><p>{e}</p><p><a href='/icloud'>Try again</a></p>")

        if not success:
            return render_2fa_form(status_msg="❌ Invalid code. Try again or resend.")

        if not api.is_trusted_session:
            try:
                api.trust_session()
            except Exception as e:
                log.warning(f"⚠️ trust_session failed: {e}")

        code_sent_at = None
        _refresh_device()
        return render_page("<h2>✅ Code accepted</h2><p>Session restored. <a href='/ping'>Try /ping</a></p>")


# ----------------------
# /icloud/resend — manual recovery: re-init + force-send code
# ----------------------
@app.post("/icloud/resend", response_class=HTMLResponse)
def resend_code():
    with state_lock:
        # Always re-init so we pick up the true current auth state
        init_api()

        if api is None:
            return render_page(f"<h2>⚠️ Cannot connect to iCloud</h2><p>{last_error}</p>")

        if not api.requires_2fa:
            return render_page("<h2>✅ Session is already authenticated.</h2><p><a href='/ping'>Try /ping</a></p>")

        ok, msg = send_code(force=True)
        if not ok:
            return render_page(f"<h2>⚠️ {msg}</h2>")
        return render_2fa_form(status_msg=msg)


# ----------------------
# /ping — trigger sound on the device
# ----------------------
@app.get("/ping")
def ping_phone():
    global device
    if device is None:
        # Startup init can fail if the network isn't up yet after a cold boot.
        # Retry here so a valid cookie session recovers without visiting /icloud.
        with state_lock:
            if device is None:
                init_api()
    if device is None:
        return JSONResponse(
            status_code=401,
            headers={"Location": "/icloud"},
            content={
                "error": "iCloud authentication required.",
                "message": "Visit /icloud to authenticate.",
                "redirect": "/icloud",
            },
        )

    try:
        device.play_sound()
    except (PyiCloudAPIResponseException, PyiCloudFailedLoginException) as e:
        log.warning(f"⚠️ Ping failed (auth issue): {e}")
        with state_lock:
            device = None  # mark stale; next /icloud visit will re-init
        return JSONResponse(
            status_code=401,
            headers={"Location": "/icloud"},
            content={
                "error": "iCloud authentication required.",
                "message": "Visit /icloud to fix authentication.",
                "redirect": "/icloud",
            },
        )

    log.info("📱 Ping sent")
    return {"status": "Ping sent to iPhone"}


# ----------------------
# App runner
# ----------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)
