"""FastAPI app: uvicorn phone_pinger.main:app"""
from contextlib import asynccontextmanager
import time

from . import logging_setup

logging_setup.configure()

from fastapi import FastAPI, Request
import uvicorn

from . import icloud
from .logging_setup import log
from .routes import ping, ui


@asynccontextmanager
async def lifespan(app: FastAPI):
    icloud.startup()
    yield


# No /docs, /redoc or /openapi.json: they'd expose a "Try it out" button for /ping.
app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(ui.router)
app.include_router(ping.router)


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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)
