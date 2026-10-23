"""List the devices on an iCloud account with their IDs, for ICLOUD_DEVICE_ID.

    .\\venv\\Scripts\\python find_devices.py

Credentials come from EMAIL / PASSWORD below if filled in, otherwise from a prompt.
It deliberately ignores the ICLOUD_* env vars: they're set machine-wide for the
production account, and silently using them is easy to miss when you meant a
different account. Never commit this file with credentials filled in.

Handles 2FA in the terminal and saves the session to icloud_cookies/ (keyed by
email), so the service or a dev instance on the same account can skip 2FA later.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

from getpass import getpass
import os

from pyicloud import PyiCloudService
from pyicloud.exceptions import PyiCloudFailedLoginException

EMAIL = ""
PASSWORD = ""

COOKIE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icloud_cookies")


def login() -> PyiCloudService:
    email = EMAIL or input("Apple ID email: ").strip()
    password = PASSWORD or getpass("Apple ID password: ")
    os.makedirs(COOKIE_DIR, exist_ok=True)

    try:
        api = PyiCloudService(email, password, cookie_directory=COOKIE_DIR, accept_terms=True)
    except PyiCloudFailedLoginException as e:
        sys.exit(f"❌ Login failed: {e}")

    if api.requires_2fa:
        if not api.request_2fa_code():
            sys.exit("❌ Apple declined to send a code (security key may be required).")
        code = input("2FA code from a trusted device: ").strip()
        if not api.validate_2fa_code(code):
            sys.exit("❌ Invalid code.")
        if not api.is_trusted_session:
            api.trust_session()
        print("✅ Verified; session saved.")

    return api


def main() -> None:
    api = login()
    devices = list(api.devices)
    if not devices:
        sys.exit("No devices on this account. Sign in to Find My on a device first.")

    print()
    for d in devices:
        c = d._content
        print(f"{c.get('deviceDisplayName', '?')} | {c.get('name', '?')}")
        print(f"    id: {c.get('id', '?')}")
    print("\nCopy the id of the target device into ICLOUD_DEVICE_ID.")


if __name__ == "__main__":
    main()
