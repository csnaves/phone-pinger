"""HTML pages for the /icloud verification UI.

Every page takes `auth`, one of "disconnected", "mfa_required" or "authenticated",
which picks the status banner. Dynamic text is escaped before it's interpolated.
"""
from html import escape

BANNERS = {
    "disconnected": ("⚠️ Not Connected", "#F44336"),
    "mfa_required": ("⚠️ MFA Required", "#FF9800"),
    "authenticated": ("✅ Authenticated", "#4CAF50"),
}

# A form, not a link: pings only go out via POST. /icloud/ping redirects back here
# with ?ping=<key>, and the key picks one of PING_RESULTS for the status line.
PING_LINK = "<form method='post' action='/icloud/ping'><button type='submit'>Ping phone</button></form>"

PING_RESULTS = {
    "sent": "📱 Ping sent",
    "failed": "⚠️ Ping failed: iCloud needs re-authentication.",
}


def layout(auth: str, content: str, status_msg: str = "") -> str:
    banner_text, banner_color = BANNERS[auth]
    status_html = f'<div class="status">{escape(status_msg)}</div>' if status_msg else ""

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


def verify_form(auth: str, status_msg: str = "") -> str:
    return layout(auth, """
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


def cannot_connect(auth: str, error: "str | None", retry: bool = False) -> str:
    retry_html = (
        "<form method='post' action='/icloud/resend'><button type='submit'>Try Again</button></form>"
        if retry else ""
    )
    return layout(auth, f"<h2>⚠️ Cannot connect to iCloud</h2><p>{escape(str(error))}</p>{retry_html}")


def already_authenticated(auth: str, status_msg: str = "") -> str:
    return layout(auth, f"<h2>✅ No verification needed.</h2>{PING_LINK}", status_msg=status_msg)


def verify_error(auth: str, error: Exception) -> str:
    return layout(auth, f"<h2>❌ Verification error</h2><p>{escape(str(error))}</p><p><a href='/icloud'>Try again</a></p>")


def code_accepted(auth: str) -> str:
    return layout(auth, f"<h2>✅ Code accepted</h2><p>Session restored.</p>{PING_LINK}")


def send_failed(auth: str, msg: str) -> str:
    return layout(auth, f"<h2>⚠️ {escape(msg)}</h2>")
