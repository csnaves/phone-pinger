"""Token checks, used as FastAPI dependencies."""
import hmac

from fastapi import HTTPException, Request

from . import config


def _check_token(request: Request, allow_cookie: bool) -> None:
    auth = request.headers.get("authorization", "")
    supplied = (
        (auth[len("Bearer "):] if auth.startswith("Bearer ") else "")
        or request.query_params.get("token", "")
        or (request.cookies.get(config.TOKEN_COOKIE, "") if allow_cookie else "")
    )
    # 403, not 401: /ping's 401 already means "iCloud needs re-auth".
    if not hmac.compare_digest(supplied.encode(), config.API_TOKEN.encode()):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")


def require_token(request: Request) -> None:
    """Header, query param, or the /icloud UI's cookie. The cookie is SameSite=Lax,
    so browsers only attach it to cross-site requests on top-level GETs."""
    _check_token(request, allow_cookie=True)


def require_url_token(request: Request) -> None:
    """For GET /ping: no cookie, so a link click or prefetch can't ring the phone."""
    _check_token(request, allow_cookie=False)
