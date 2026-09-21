"""API package: FastAPI app factory and WS/REST entrypoints."""

from aizen.api.main import build_default_loop, create_app

__all__ = ["build_default_loop", "create_app"]
