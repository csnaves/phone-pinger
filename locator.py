import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from pyicloud import PyiCloudService
from pyicloud.exceptions import PyiCloudAPIResponseException, PyiCloudFailedLoginException
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
import uvicorn
import pages
from datetime import datetime, timedelta
import hmac
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

# Shared secret required on every endpoint. Sent as "Authorization: Bearer <token>"
# or ?token=<token>; the /icloud UI swaps the query param for a cookie.
API_TOKEN = os.environ["LOCATOR_TOKEN"]
TOKEN_COOKIE = "locator_token"
TOKEN_COOKIE_MAX_AGE = 30 * 24 * 3600

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
# No /docs, /redoc or /openapi.json: they'd expose a "Try it out" button for /ping.
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def _check_token(request: Request, allow_cookie: bool) -> None:
    auth = request.headers.get("authorization", "")
    supplied = (
        (auth[len("Bearer "):] if auth.startswith("Bearer ") else "")
        or request.query_params.get("token", "")
        or (request.cookies.get(TOKEN_COOKIE, "") if allow_cookie else "")
    )
    # 403, not 401: /ping's 401 already means "iCloud needs re-auth".
    if not hmac.compare_digest(supplied.encode(), API_TOKEN.encode()):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")


def require_token(request: Request) -> None:
    """Header, query param, or the /icloud UI's cookie. The cookie is SameSite=Lax,
    so browsers only attach it to cross-site requests on top-level GETs."""
    _check_token(request, allow_cookie=True)


def require_url_token(request: Request) -> None:
    """For GET /ping: no cookie, so a link click or prefetch can't ring the phone."""
    _check_token(request, allow_cookie=False)


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


def auth_state() -> str:
    """Banner state for pages.*. Read while holding state_lock."""
    if api is None:
        return "disconnected"
    if device is None or api.requires_2fa:
        return "mfa_required"
    return "authenticated"


# ----------------------
# /icloud — verification UI
# ----------------------
@app.get("/icloud", response_class=HTMLResponse, dependencies=[Depends(require_token)])
def icloud_ui(request: Request):
    if "token" in request.query_params:
        # Move the token into a cookie so it doesn't sit in the URL bar and history,
        # and so the page's forms authenticate.
        response = RedirectResponse("/icloud", status_code=303)
        response.set_cookie(
            TOKEN_COOKIE, API_TOKEN, max_age=TOKEN_COOKIE_MAX_AGE, httponly=True, samesite="lax"
        )
        return response

    # Fixed strings only: the query param picks a message, it's never echoed.
    ping_msg = pages.PING_RESULTS.get(request.query_params.get("ping", ""), "")

    with state_lock:
        if api is None or device is None:
            init_api()

        if api is None:
            return pages.cannot_connect(auth_state(), last_error, retry=True)

        if not api.requires_2fa and device is not None:
            return pages.already_authenticated(auth_state(), status_msg=ping_msg)

        # 2FA required: auto-send a code (rate-limited so refreshes don't spam Apple)
        _, msg = send_code(force=False)
        return pages.verify_form(auth_state(), status_msg=" ".join(filter(None, [ping_msg, msg])))


@app.post("/icloud", response_class=HTMLResponse, dependencies=[Depends(require_token)])
def submit_code(code: str = Form(...)):
    global code_sent_at
    with state_lock:
        if api is None:
            return pages.cannot_connect(auth_state(), last_error)

        if not api.requires_2fa:
            _refresh_device()
            return pages.already_authenticated(auth_state())

        try:
            success = api.validate_2fa_code(code)
        except Exception as e:
            return pages.verify_error(auth_state(), e)

        if not success:
            return pages.verify_form(auth_state(), status_msg="❌ Invalid code. Try again or resend.")

        if not api.is_trusted_session:
            try:
                api.trust_session()
            except Exception as e:
                log.warning(f"⚠️ trust_session failed: {e}")

        code_sent_at = None
        _refresh_device()
        return pages.code_accepted(auth_state())


# ----------------------
# /icloud/resend — manual recovery: re-init + force-send code
# ----------------------
@app.post("/icloud/resend", response_class=HTMLResponse, dependencies=[Depends(require_token)])
def resend_code():
    with state_lock:
        # Always re-init so we pick up the true current auth state
        init_api()

        if api is None:
            return pages.cannot_connect(auth_state(), last_error)

        if not api.requires_2fa:
            return pages.already_authenticated(auth_state())

        ok, msg = send_code(force=True)
        if not ok:
            return pages.send_failed(auth_state(), msg)
        return pages.verify_form(auth_state(), status_msg=msg)


# ----------------------
# /icloud/ping — the page's "Ping phone" button
# Redirects back to /icloud with the result (post/redirect/get), so refreshing
# the page shows the status again instead of re-sending the ping.
# ----------------------
@app.post("/icloud/ping", dependencies=[Depends(require_token)])
def ping_from_page():
    result = "sent" if play_sound() else "failed"
    return RedirectResponse(f"/icloud?ping={result}", status_code=303)


# ----------------------
# /ping — trigger sound on the device
# POST for Shortcuts/curl and the /icloud page. GET only for the Shelly button,
# which can't send POST, and only with ?token= (see require_url_token).
# ----------------------
def play_sound() -> bool:
    """Ring the target device. Returns False if iCloud needs re-authentication."""
    global device
    if device is None:
        # Startup init can fail if the network isn't up yet after a cold boot.
        # Retry here so a valid cookie session recovers without visiting /icloud.
        with state_lock:
            if device is None:
                init_api()
    if device is None:
        return False

    try:
        device.play_sound()
    except (PyiCloudAPIResponseException, PyiCloudFailedLoginException) as e:
        log.warning(f"⚠️ Ping failed (auth issue): {e}")
        with state_lock:
            device = None  # mark stale; next /icloud visit will re-init
        return False

    log.info("📱 Ping sent")
    return True


@app.post("/ping", dependencies=[Depends(require_token)])
def ping_phone():
    if not play_sound():
        return JSONResponse(
            status_code=401,
            headers={"Location": "/icloud"},
            content={
                "error": "iCloud authentication required.",
                "message": "Visit /icloud to authenticate.",
                "redirect": "/icloud",
            },
        )
    return {"status": "Ping sent to iPhone"}


@app.get("/ping", dependencies=[Depends(require_url_token)])
def ping_phone_get():
    return ping_phone()


# ----------------------
# App runner
# ----------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)
