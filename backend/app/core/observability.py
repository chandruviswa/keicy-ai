"""Structured JSON logs with a request id on every line, and safe error responses.

Every HTTP response carries `X-Request-ID` (taken from the incoming header if present). Log lines
emitted while handling a request include that id, so one request can be traced through the API,
agents, tools, LLM calls and PayPal calls.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextvars import ContextVar

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, OperationalError
from starlette.types import ASGIApp, Receive, Scope, Send

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
log = logging.getLogger("keicy.http")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if rid := request_id_var.get():
            out["request_id"] = rid
        for key in ("method", "path", "status", "duration_ms", "user_id", "client"):
            if hasattr(record, key):
                out[key] = getattr(record, key)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def configure_logging(fmt: str = "json", level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if fmt == "json" else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").disabled = True  # replaced by our request log line
    logging.getLogger("httpx").setLevel(logging.WARNING)


class RequestContextMiddleware:
    """Pure ASGI (works for HTTP; WebSockets pass through untouched)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        incoming = headers.get(b"x-request-id", b"").decode()[:64]
        rid = incoming if incoming and incoming.replace("-", "").isalnum() else uuid.uuid4().hex
        token = request_id_var.set(rid)
        scope.setdefault("state", {})["request_id"] = rid
        started = time.perf_counter()
        status = 500

        started_response = False

        async def send_wrapper(message):
            nonlocal status, started_response
            if message["type"] == "http.response.start":
                status, started_response = message["status"], True
                message.setdefault("headers", []).append((b"x-request-id", rid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # Unhandled error: answer here (inside the request context) so the 500 still carries the
            # request id — and never leak internals (stack traces, SQL, secrets) to the client.
            log.exception("Unhandled error")
            if not started_response:
                body = json.dumps({"detail": "Internal server error", "request_id": rid}).encode()
                await send_wrapper({"type": "http.response.start", "status": 500,
                                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                await send_wrapper({"type": "http.response.body", "body": body})
        finally:
            state = scope.get("state") or {}
            log.info("%s %s %s", scope["method"], scope["path"], status, extra={
                "method": scope["method"], "path": scope["path"], "status": status,
                "duration_ms": round((time.perf_counter() - started) * 1000), "user_id": state.get("user_id"),
            })
            request_id_var.reset(token)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(OperationalError)
    @app.exception_handler(ConnectionRefusedError)
    @app.exception_handler(ConnectionError)
    async def db_unavailable(request: Request, exc: Exception):
        log.error("Dependency unavailable: %s", exc)
        return JSONResponse({"detail": "A backing service is unavailable — please try again shortly", "request_id": request_id_var.get()},
                            status_code=503)

    @app.exception_handler(DBAPIError)
    async def db_error(request: Request, exc: DBAPIError):
        log.exception("Database error")
        return JSONResponse({"detail": "Database error", "request_id": request_id_var.get()}, status_code=500)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        # Never leak internals (stack traces, SQL, secrets) to the client — the request id links to the log.
        log.exception("Unhandled error")
        return JSONResponse({"detail": "Internal server error", "request_id": request_id_var.get()}, status_code=500)
