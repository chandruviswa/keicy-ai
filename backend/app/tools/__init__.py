"""Importing this package registers every tool on `registry`."""

from app.tools import catalog, finance, payments, procurement  # noqa: F401
from app.tools.common import registry

__all__ = ["registry"]
