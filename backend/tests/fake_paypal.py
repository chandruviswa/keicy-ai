"""Tests use the same simulator the app runs in PAYPAL_MODE=mock."""

from app.payments.mock import MockPayPal as FakePayPal

__all__ = ["FakePayPal"]
