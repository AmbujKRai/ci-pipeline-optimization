"""Entry point: `python -m app` starts the web server on $PORT (default 8000)."""

from __future__ import annotations

import logging
import os

import uvicorn


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        # Inside a container the server must listen on every interface to be reachable.
        host=os.getenv("HOST", "0.0.0.0"),  # nosec B104
        port=int(os.getenv("PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
