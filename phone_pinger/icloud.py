"""iCloud session state and the operations on it.

Other modules read the state as icloud.api / icloud.device (never `from icloud
import api`, which would copy a stale reference) and change it only through the
functions here.
"""
from datetime import datetime
import threading

from pyicloud import PyiCloudService
from pyicloud.exceptions import PyiCloudAPIResponseException, PyiCloudFailedLoginException

from . import config
from .logging_setup import log

# ----------------------
# Module state (mutate only while holding state_lock)
# ----------------------
api: "PyiCloudService | None" = None
device = None
last_error: "str | None" = None
code_sent_at: "datetime | None" = None
state_lock = threading.Lock()


def refresh_device() -> None:
    global device
    if api is None:
        device = None
        return
    try:
        match = next((d for d in api.devices if d._content.get("id") == config.DEVICE_ID), None)
        if match is None:
            log.warning(f"⚠️ No device with id {config.DEVICE_ID[:12]}... in account")
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
            config.ICLOUD_EMAIL,
            config.ICLOUD_PASSWORD,
            cookie_directory=str(config.COOKIE_DIR),
            accept_terms=True,
        )
        last_error = None
        code_sent_at = None
        refresh_device()
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
        if elapsed < config.MIN_RESEND_INTERVAL:
            wait = int((config.MIN_RESEND_INTERVAL - elapsed).total_seconds())
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


def mark_verified() -> None:
    """After a 2FA code is accepted. Caller must hold state_lock."""
    global code_sent_at
    code_sent_at = None
    refresh_device()


def auth_state() -> str:
    """Banner state for pages.*. Read while holding state_lock."""
    if api is None:
        return "disconnected"
    if device is None or api.requires_2fa:
        return "mfa_required"
    return "authenticated"


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


def startup() -> None:
    """Best-effort login at service start. Never raises."""
    config.COOKIE_DIR.mkdir(exist_ok=True)
    log.info(f"Using cookie dir: {config.COOKIE_DIR}")
    with state_lock:
        init_api()
        send_code()
