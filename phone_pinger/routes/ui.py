"""/icloud: the verification page and its buttons."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import config, icloud, pages
from ..auth import require_token
from ..logging_setup import log

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/icloud", response_class=HTMLResponse)
def icloud_ui(request: Request):
    if "token" in request.query_params:
        # Move the token into a cookie so it doesn't sit in the URL bar and history,
        # and so the page's forms authenticate.
        response = RedirectResponse("/icloud", status_code=303)
        response.set_cookie(
            config.TOKEN_COOKIE, config.API_TOKEN,
            max_age=config.TOKEN_COOKIE_MAX_AGE, httponly=True, samesite="lax",
        )
        return response

    # Fixed strings only: the query param picks a message, it's never echoed.
    ping_msg = pages.PING_RESULTS.get(request.query_params.get("ping", ""), "")

    with icloud.state_lock:
        if icloud.api is None or icloud.device is None:
            icloud.init_api()

        if icloud.api is None:
            return pages.cannot_connect(icloud.auth_state(), icloud.last_error, retry=True)

        if not icloud.api.requires_2fa and icloud.device is not None:
            return pages.already_authenticated(icloud.auth_state(), status_msg=ping_msg)

        # 2FA required: auto-send a code (rate-limited so refreshes don't spam Apple)
        _, msg = icloud.send_code(force=False)
        return pages.verify_form(icloud.auth_state(), status_msg=" ".join(filter(None, [ping_msg, msg])))


@router.post("/icloud", response_class=HTMLResponse)
def submit_code(code: str = Form(...)):
    with icloud.state_lock:
        api = icloud.api
        if api is None:
            return pages.cannot_connect(icloud.auth_state(), icloud.last_error)

        if not api.requires_2fa:
            icloud.refresh_device()
            return pages.already_authenticated(icloud.auth_state())

        try:
            success = api.validate_2fa_code(code)
        except Exception as e:
            return pages.verify_error(icloud.auth_state(), e)

        if not success:
            return pages.verify_form(icloud.auth_state(), status_msg="❌ Invalid code. Try again or resend.")

        if not api.is_trusted_session:
            try:
                api.trust_session()
            except Exception as e:
                log.warning(f"⚠️ trust_session failed: {e}")

        icloud.mark_verified()
        return pages.code_accepted(icloud.auth_state())


# Manual recovery: re-init + force-send code
@router.post("/icloud/resend", response_class=HTMLResponse)
def resend_code():
    with icloud.state_lock:
        # Always re-init so we pick up the true current auth state
        icloud.init_api()

        if icloud.api is None:
            return pages.cannot_connect(icloud.auth_state(), icloud.last_error)

        if not icloud.api.requires_2fa:
            return pages.already_authenticated(icloud.auth_state())

        ok, msg = icloud.send_code(force=True)
        if not ok:
            return pages.send_failed(icloud.auth_state(), msg)
        return pages.verify_form(icloud.auth_state(), status_msg=msg)


# The page's "Ping phone" button. Redirects back to /icloud with the result
# (post/redirect/get), so refreshing the page shows the status again instead of
# re-sending the ping.
@router.post("/icloud/ping")
def ping_from_page():
    result = "sent" if icloud.play_sound() else "failed"
    return RedirectResponse(f"/icloud?ping={result}", status_code=303)
