"""Settings, read once from the environment at import.

Missing required variables fail at startup with a clear message, instead of at the
first request that needs them.
"""
from datetime import timedelta
from pathlib import Path
import os


def _require(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"Missing required environment variable {name} (see README).")
    return value


# Anchored to the repo root, not this package, so the saved sessions in the
# gitignored icloud_cookies/ keep being used.
REPO_ROOT = Path(__file__).resolve().parent.parent
COOKIE_DIR = REPO_ROOT / "icloud_cookies"

ICLOUD_EMAIL = _require("ICLOUD_EMAIL")
ICLOUD_PASSWORD = _require("ICLOUD_PASSWORD")

# Stable iCloud device id of the target phone. Immune to iCloud reordering/renames;
# only changes if the phone is wiped and re-added to the account.
DEVICE_ID = _require("ICLOUD_DEVICE_ID")

# Shared secret required on every endpoint. Sent as "Authorization: Bearer <token>"
# or ?token=<token>; the /icloud UI swaps the query param for a cookie.
API_TOKEN = _require("LOCATOR_TOKEN")
TOKEN_COOKIE = "locator_token"
TOKEN_COOKIE_MAX_AGE = 30 * 24 * 3600

MIN_RESEND_INTERVAL = timedelta(seconds=30)
