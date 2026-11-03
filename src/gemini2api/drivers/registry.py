from __future__ import annotations

from ..config import Settings
from .base import GeminiDriver


def create_driver(settings: Settings) -> GeminiDriver:
    """Instantiate the driver selected by ``GEMINI2API_DRIVER``. Imports are lazy so that
    optional heavy dependencies of one driver never affect the others."""
    if settings.driver == "mock":
        from .mock import MockDriver

        return MockDriver()
    if settings.driver == "browser":
        from .browser.driver import BrowserDriver

        return BrowserDriver(settings)
    if settings.driver == "http":
        from .http.driver import HttpDriver

        return HttpDriver(settings)
    raise ValueError(f"unknown driver: {settings.driver}")
