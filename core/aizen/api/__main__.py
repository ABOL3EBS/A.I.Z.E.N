"""`python -m aizen.api` — run the dev API server."""

from __future__ import annotations

import uvicorn

from aizen.api import create_app
from aizen.config import Settings


def main() -> None:
    settings = Settings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.api_host, port=settings.api_port, log_level="info")


if __name__ == "__main__":
    main()
