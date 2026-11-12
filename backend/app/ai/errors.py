from enum import StrEnum


class LLMErrorKind(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    UNAVAILABLE = "unavailable"  # connection refused, 5xx, model not loaded
    AUTH = "auth"  # bad / missing key
    BAD_REQUEST = "bad_request"  # our request is wrong (4xx other than 401/403/429)
    INVALID_OUTPUT = "invalid_output"  # reply wasn't valid JSON / didn't match schema
    NOT_CONFIGURED = "not_configured"


# Retry the same provider (with backoff) only for transient problems.
RETRYABLE = {LLMErrorKind.TIMEOUT, LLMErrorKind.RATE_LIMIT, LLMErrorKind.UNAVAILABLE}


class LLMError(Exception):
    def __init__(self, kind: LLMErrorKind, message: str, provider: str | None = None) -> None:
        super().__init__(f"[{provider or 'llm'}:{kind}] {message}")
        self.kind = kind
        self.provider = provider
        self.message = message

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE


class LLMGatewayError(Exception):
    """Every provider failed. `errors` holds the last error per provider, in order tried."""

    def __init__(self, errors: list[LLMError]) -> None:
        detail = "; ".join(str(e) for e in errors) or "no LLM providers configured"
        super().__init__(f"All LLM providers failed: {detail}")
        self.errors = errors


def kind_from_status(status: int | None) -> LLMErrorKind:
    if status in (401, 403):
        return LLMErrorKind.AUTH
    if status == 429:
        return LLMErrorKind.RATE_LIMIT
    if status == 408:
        return LLMErrorKind.TIMEOUT
    if status is not None and status >= 500:
        return LLMErrorKind.UNAVAILABLE
    if status == 404:
        return LLMErrorKind.UNAVAILABLE  # e.g. unknown model id — another provider may work
    return LLMErrorKind.BAD_REQUEST
