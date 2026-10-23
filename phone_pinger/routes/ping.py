"""/ping: ring the phone from Shortcuts, curl or the Shelly button.

POST for Shortcuts/curl. GET only for the Shelly button, which can't send POST,
and only with ?token= (see auth.require_url_token).
"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from .. import icloud
from ..auth import require_token, require_url_token

router = APIRouter()


@router.post("/ping", dependencies=[Depends(require_token)])
def ping_phone():
    if not icloud.play_sound():
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


@router.get("/ping", dependencies=[Depends(require_url_token)])
def ping_phone_get():
    return ping_phone()
