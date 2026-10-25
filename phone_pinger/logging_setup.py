import logging
import sys

LOG_FORMAT = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")

log = logging.getLogger("phone_pinger")


def configure() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    log.setLevel(logging.INFO)
    log.propagate = False
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(LOG_FORMAT)
    log.addHandler(handler)

    # The uvicorn CLI configures its loggers before importing the app, so restamp
    # its startup/shutdown/error output and drop its access log in favor of
    # main.log_requests.
    for h in logging.getLogger("uvicorn").handlers:
        h.setFormatter(LOG_FORMAT)
    logging.getLogger("uvicorn.access").disabled = True
