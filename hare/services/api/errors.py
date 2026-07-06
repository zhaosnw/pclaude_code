"""
API error classification, user-facing error messages, and taxonomy.

Port of: src/services/api/errors.ts (1218 lines)

Provides:
- Exception hierarchy (APIError, RateLimitError, OverloadedError, etc.)
- Error message constants
- Predicate functions for detecting error types
- User-facing error message factories
- get_assistant_message_from_error() — ~30-branch central dispatch
- classify_api_error() — 25+ taxonomy strings for analytics
- categorize_retryable_api_error() — for SDK agent use
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class APIError(Exception):
    """Base API error."""

    def __init__(self, message: str, status_code: int = 0, **kwargs: Any):
        super().__init__(message)
        self.status_code = status_code
        self.error_type: str = kwargs.get("error_type", "api_error")
        self.headers: dict[str, str] = kwargs.get("headers", {})


class RateLimitError(APIError):
    """Rate limit error (429)."""

    def __init__(self, message: str = "Rate limit exceeded", retry_after: float = 0):
        super().__init__(message, status_code=429)
        self.retry_after = retry_after


class OverloadedError(APIError):
    """Server overloaded (529)."""

    def __init__(self, message: str = "API overloaded"):
        super().__init__(message, status_code=529)


class AuthenticationError(APIError):
    """Authentication failed (401)."""

    def __init__(self, message: str = "Invalid API key"):
        super().__init__(message, status_code=401)


class InsufficientCreditsError(APIError):
    """Insufficient credits."""

    def __init__(self, message: str = "Insufficient credits"):
        super().__init__(message, status_code=402)


class CannotRetryError(Exception):
    """Unrecoverable error — should not be retried."""

    def __init__(self, message: str, original_error: Exception | None = None):
        super().__init__(message)
        self.original_error = original_error


class FallbackTriggeredError(Exception):
    """Model fallback was triggered (original → fallback model)."""

    def __init__(self, original_model: str, fallback_model: str):
        super().__init__(
            f"Falling back from {original_model} to {fallback_model}"
        )
        self.original_model = original_model
        self.fallback_model = fallback_model


# ---------------------------------------------------------------------------
# Error message constants
# ---------------------------------------------------------------------------

API_ERROR_MESSAGE_PREFIX = "API Error"
PROMPT_TOO_LONG_ERROR_MESSAGE = "Prompt is too long"
CREDIT_BALANCE_TOO_LOW_ERROR_MESSAGE = "Credit balance is too low"
INVALID_API_KEY_ERROR_MESSAGE = "Not logged in · Please run /login"
INVALID_API_KEY_ERROR_MESSAGE_EXTERNAL = (
    "Invalid API key · Fix external API key"
)
ORG_DISABLED_ERROR_MESSAGE_ENV_KEY_WITH_OAUTH = (
    "Your ANTHROPIC_API_KEY belongs to a disabled organization · "
    "Unset the environment variable to use your subscription instead"
)
ORG_DISABLED_ERROR_MESSAGE_ENV_KEY = (
    "Your ANTHROPIC_API_KEY belongs to a disabled organization · "
    "Update or unset the environment variable"
)
TOKEN_REVOKED_ERROR_MESSAGE = "OAuth token revoked · Please run /login"
CCR_AUTH_ERROR_MESSAGE = (
    "Authentication error · This may be a temporary network issue, please try again"
)
REPEATED_529_ERROR_MESSAGE = "Repeated 529 Overloaded errors"
CUSTOM_OFF_SWITCH_MESSAGE = (
    "Opus is experiencing high load, please use /model to switch to Sonnet"
)
API_TIMEOUT_ERROR_MESSAGE = "Request timed out"
OAUTH_ORG_NOT_ALLOWED_ERROR_MESSAGE = (
    "Your account does not have access to Claude Code. Please run /login."
)

# Default PDF limits (matches TS constants/apiLimits.ts)
API_PDF_MAX_PAGES = 100
PDF_TARGET_RAW_SIZE = 32 * 1024 * 1024  # 32 MB


# ---------------------------------------------------------------------------
# Predicate / detection functions
# ---------------------------------------------------------------------------


def starts_with_api_error_prefix(text: str) -> bool:
    """Check if text starts with the API error prefix."""
    return text.startswith(API_ERROR_MESSAGE_PREFIX) or text.startswith(
        f"Please run /login · {API_ERROR_MESSAGE_PREFIX}"
    )


def is_prompt_too_long_message(msg: Any) -> bool:
    """Check if an AssistantMessage is a prompt-too-long error."""
    if not getattr(msg, "is_api_error_message", False):
        return False
    content = getattr(getattr(msg, "message", None), "content", None)
    if not isinstance(content, list):
        return False
    return any(
        b.get("type") == "text" and str(b.get("text", "")).startswith(PROMPT_TOO_LONG_ERROR_MESSAGE)
        for b in content
        if isinstance(b, dict)
    )


@dataclass
class TokenCounts:
    actual_tokens: Optional[int] = None
    limit_tokens: Optional[int] = None


def parse_prompt_too_long_token_counts(raw_message: str) -> TokenCounts:
    """Parse token counts from prompt-too-long error messages.

    Lenient parsing handles Vertex casing differences and SDK prefixes.
    """
    match = re.search(
        r"prompt is too long[^0-9]*(\d+)\s*tokens?\s*>\s*(\d+)",
        raw_message,
        re.IGNORECASE,
    )
    if match:
        return TokenCounts(
            actual_tokens=int(match.group(1)),
            limit_tokens=int(match.group(2)),
        )
    return TokenCounts()


def get_prompt_too_long_token_gap(msg: Any) -> Optional[int]:
    """Return tokens-over-limit for reactive compact to skip multiple groups."""
    if not is_prompt_too_long_message(msg):
        return None
    error_details = getattr(msg, "error_details", None)
    if not error_details:
        return None
    tc = parse_prompt_too_long_token_counts(str(error_details))
    if tc.actual_tokens is None or tc.limit_tokens is None:
        return None
    gap = tc.actual_tokens - tc.limit_tokens
    return gap if gap > 0 else None


def is_media_size_error(raw: str) -> bool:
    """Check if raw error text is a media-size rejection that stripping can fix."""
    return (
        ("image exceeds" in raw and "maximum" in raw)
        or ("image dimensions exceed" in raw and "many-image" in raw)
        or bool(re.search(r"maximum of \d+ PDF pages", raw))
    )


def is_media_size_error_message(msg: Any) -> bool:
    """Message-level predicate for media-size rejection."""
    return (
        getattr(msg, "is_api_error_message", False) is True
        and getattr(msg, "error_details", None) is not None
        and is_media_size_error(str(getattr(msg, "error_details", "")))
    )


def is_valid_api_message(value: Any) -> bool:
    """Type guard for valid API message responses."""
    return (
        isinstance(value, dict)
        and "content" in value
        and "model" in value
        and "usage" in value
        and isinstance(value.get("content"), list)
        and isinstance(value.get("model"), str)
        and isinstance(value.get("usage"), dict)
    )


def extract_unknown_error_format(value: Any) -> Optional[str]:
    """Extract known error types from malformed API responses."""
    if not isinstance(value, dict):
        return None
    output = value.get("Output")
    if isinstance(output, dict):
        return output.get("__type")
    return None


# ---------------------------------------------------------------------------
# Error message factories
# ---------------------------------------------------------------------------


def _is_non_interactive() -> bool:
    """Check if running in non-interactive / headless mode."""
    try:
        from hare.bootstrap.state import get_is_non_interactive_session
        return get_is_non_interactive_session()
    except Exception:
        return False


def _is_ccr_mode() -> bool:
    """Check if in CCR (Claude Code Remote) mode where JWTs handle auth."""
    return os.environ.get("CLAUDE_CODE_REMOTE", "") == "1"


def _format_file_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} bytes"


def get_pdf_too_large_error_message() -> str:
    limits = f"max {API_PDF_MAX_PAGES} pages, {_format_file_size(PDF_TARGET_RAW_SIZE)}"
    if _is_non_interactive():
        return (
            f"PDF too large ({limits}). Try reading the file a different way "
            f"(e.g., extract text with pdftotext)."
        )
    return (
        f"PDF too large ({limits}). Double press esc to go back and try again, "
        f"or use pdftotext to convert to text first."
    )


def get_pdf_password_protected_error_message() -> str:
    if _is_non_interactive():
        return "PDF is password protected. Try using a CLI tool to extract or convert the PDF."
    return "PDF is password protected. Please double press esc to edit your message and try again."


def get_pdf_invalid_error_message() -> str:
    if _is_non_interactive():
        return "The PDF file was not valid. Try converting it to text first (e.g., pdftotext)."
    return "The PDF file was not valid. Double press esc to go back and try again with a different file."


def get_image_too_large_error_message() -> str:
    if _is_non_interactive():
        return "Image was too large. Try resizing the image or using a different approach."
    return "Image was too large. Double press esc to go back and try again with a smaller image."


def get_request_too_large_error_message() -> str:
    limits = f"max {_format_file_size(PDF_TARGET_RAW_SIZE)}"
    if _is_non_interactive():
        return f"Request too large ({limits}). Try with a smaller file."
    return f"Request too large ({limits}). Double press esc to go back and try with a smaller file."


def get_token_revoked_error_message() -> str:
    if _is_non_interactive():
        return (
            "Your account does not have access to Claude. "
            "Please login again or contact your administrator."
        )
    return TOKEN_REVOKED_ERROR_MESSAGE


def get_oauth_org_not_allowed_error_message() -> str:
    if _is_non_interactive():
        return (
            "Your organization does not have access to Claude. "
            "Please login again or contact your administrator."
        )
    return OAUTH_ORG_NOT_ALLOWED_ERROR_MESSAGE


def _get_3p_model_fallback_suggestion(model: str) -> Optional[str]:
    """Suggest a fallback model for 3P users when the selected model is unavailable."""
    m = model.lower()
    if "opus-4-6" in m or "opus_4_6" in m:
        return _resolve_model_string("opus41")
    if "sonnet-4-6" in m or "sonnet_4_6" in m:
        return _resolve_model_string("sonnet45")
    if "sonnet-4-5" in m or "sonnet_4_5" in m:
        return _resolve_model_string("sonnet40")
    return None


def _resolve_model_string(key: str) -> str:
    """Resolve a known model string. Falls back to the key itself."""
    try:
        from hare.utils.model import MODEL_STRINGS
        return getattr(MODEL_STRINGS, key, key)
    except Exception:
        return key


# ---------------------------------------------------------------------------
# Core: get_assistant_message_from_error (central dispatch, ~30 branches)
# ---------------------------------------------------------------------------


def get_assistant_message_from_error(
    error: Any,
    model: str = "",
    messages: Optional[list[Any]] = None,
    messages_for_api: Optional[list[Any]] = None,
) -> Any:
    """Convert any API error into an AssistantMessage for the conversation.

    This is the central error-to-user-message converter with ~30 branches
    covering timeouts, image/PDF limits, rate limits, auth failures,
    credit balance, invalid models, and more.

    Returns an AssistantMessage (typically created via
    create_assistant_api_error_message from utils.messages).
    """
    from hare.utils.messages import create_assistant_api_error_message

    error_message = str(error) if error else ""
    error_message_lower = error_message.lower()

    # 1. SDK timeout errors
    if _is_timeout_error(error):
        return create_assistant_api_error_message(
            content=API_TIMEOUT_ERROR_MESSAGE,
            error="unknown",
        )

    # 2. Image size/resize errors (thrown before API call during validation)
    if _is_image_size_error(error):
        return create_assistant_api_error_message(
            content=get_image_too_large_error_message(),
        )

    # 3. Emergency capacity off-switch for Opus
    if CUSTOM_OFF_SWITCH_MESSAGE in error_message:
        return create_assistant_api_error_message(
            content=CUSTOM_OFF_SWITCH_MESSAGE,
            error="rate_limit",
        )

    # 4. 429 Rate limit with subscriber processing
    if _get_status_code(error) == 429:
        return _handle_429_error(error, error_message)

    # 5. Prompt too long
    if "prompt is too long" in error_message_lower:
        return create_assistant_api_error_message(
            content=PROMPT_TOO_LONG_ERROR_MESSAGE,
            error="invalid_request",
            error_details=error_message,
        )

    # 6. PDF page limit errors
    if re.search(r"maximum of \d+ PDF pages", error_message):
        return create_assistant_api_error_message(
            content=get_pdf_too_large_error_message(),
            error="invalid_request",
            error_details=error_message,
        )

    # 7. Password-protected PDF
    if "The PDF specified is password protected" in error_message:
        return create_assistant_api_error_message(
            content=get_pdf_password_protected_error_message(),
            error="invalid_request",
        )

    # 8. Invalid PDF
    if "The PDF specified was not valid" in error_message:
        return create_assistant_api_error_message(
            content=get_pdf_invalid_error_message(),
            error="invalid_request",
        )

    # 9. Image size error from API
    if (
        _get_status_code(error) == 400
        and "image exceeds" in error_message
        and "maximum" in error_message
    ):
        return create_assistant_api_error_message(
            content=get_image_too_large_error_message(),
            error_details=error_message,
        )

    # 10. Many-image dimension errors
    if (
        _get_status_code(error) == 400
        and "image dimensions exceed" in error_message
        and "many-image" in error_message
    ):
        hint = (
            "Start a new session with fewer images."
            if _is_non_interactive()
            else "Run /compact to remove old images from context, or start a new session."
        )
        return create_assistant_api_error_message(
            content=(
                f"An image in the conversation exceeds the dimension limit "
                f"for many-image requests (2000px). {hint}"
            ),
            error="invalid_request",
            error_details=error_message,
        )

    # 11. AFK mode beta header rejection
    # AFK_MODE_BETA_HEADER is '' in non-TRANSCRIPT_CLASSIFIER builds (inert)
    _afk_beta = os.environ.get("CLAUDE_CODE_AFK_MODE_BETA_HEADER", "")
    if (
        _afk_beta
        and _get_status_code(error) == 400
        and _afk_beta in error_message
        and "anthropic-beta" in error_message
    ):
        return create_assistant_api_error_message(
            content="Auto mode is unavailable for your plan",
            error="invalid_request",
        )

    # 12. Request too large (413)
    if _get_status_code(error) == 413:
        return create_assistant_api_error_message(
            content=get_request_too_large_error_message(),
            error="invalid_request",
        )

    # 13. Tool use/tool result concurrency error
    if (
        _get_status_code(error) == 400
        and "tool_use` ids were found without `tool_result` blocks immediately after"
        in error_message
    ):
        rewind_hint = "" if _is_non_interactive() else " Run /rewind to recover the conversation."
        return create_assistant_api_error_message(
            content=f"API Error: 400 due to tool use concurrency issues.{rewind_hint}",
            error="invalid_request",
        )

    # 14. Unexpected tool_use_id in tool_result (log only, no return)
    if (
        _get_status_code(error) == 400
        and "unexpected `tool_use_id` found in `tool_result`" in error_message
    ):
        pass  # Falls through to generic error handling below

    # 15. Duplicate tool_use IDs
    if (
        _get_status_code(error) == 400
        and "tool_use` ids must be unique" in error_message
    ):
        rewind_hint = "" if _is_non_interactive() else " Run /rewind to recover the conversation."
        return create_assistant_api_error_message(
            content=f"API Error: 400 duplicate tool_use ID in conversation history.{rewind_hint}",
            error="invalid_request",
            error_details=error_message,
        )

    # 16. Invalid model name — subscription users trying Opus
    if (
        _get_status_code(error) == 400
        and "invalid model name" in error_message_lower
        and model
    ):
        if _is_opus_model(model):
            return create_assistant_api_error_message(
                content=(
                    "Claude Opus is not available with the Claude Pro plan. "
                    "If you have updated your subscription plan recently, "
                    "run /logout and /login for the plan to take effect."
                ),
                error="invalid_request",
            )

    # 17. Credit balance too low
    if "Your credit balance is too low" in error_message:
        return create_assistant_api_error_message(
            content=CREDIT_BALANCE_TOO_LOW_ERROR_MESSAGE,
            error="billing_error",
        )

    # 18. Organization disabled (stale API key)
    if (
        _get_status_code(error) == 400
        and "organization has been disabled" in error_message_lower
    ):
        if os.environ.get("ANTHROPIC_API_KEY"):
            return create_assistant_api_error_message(
                error="invalid_request",
                content=ORG_DISABLED_ERROR_MESSAGE_ENV_KEY,
            )

    # 19. x-api-key errors
    if "x-api-key" in error_message_lower:
        if _is_ccr_mode():
            return create_assistant_api_error_message(
                error="authentication_failed",
                content=CCR_AUTH_ERROR_MESSAGE,
            )
        # External key source check
        is_external = bool(os.environ.get("ANTHROPIC_API_KEY"))
        return create_assistant_api_error_message(
            error="authentication_failed",
            content=(
                INVALID_API_KEY_ERROR_MESSAGE_EXTERNAL
                if is_external
                else INVALID_API_KEY_ERROR_MESSAGE
            ),
        )

    # 20. OAuth token revoked
    if (
        _get_status_code(error) == 403
        and "OAuth token has been revoked" in error_message
    ):
        return create_assistant_api_error_message(
            error="authentication_failed",
            content=get_token_revoked_error_message(),
        )

    # 21. OAuth organization not allowed
    if (
        _get_status_code(error) in (401, 403)
        and "OAuth authentication is currently not allowed for this organization"
        in error_message
    ):
        return create_assistant_api_error_message(
            error="authentication_failed",
            content=get_oauth_org_not_allowed_error_message(),
        )

    # 22. Generic 401/403 auth errors
    if _get_status_code(error) in (401, 403):
        if _is_ccr_mode():
            return create_assistant_api_error_message(
                error="authentication_failed",
                content=CCR_AUTH_ERROR_MESSAGE,
            )
        prefix = "" if _is_non_interactive() else "Please run /login · "
        return create_assistant_api_error_message(
            error="authentication_failed",
            content=f"{prefix}{API_ERROR_MESSAGE_PREFIX}: {error_message}",
        )

    # 23. Bedrock model ID errors
    if (
        os.environ.get("CLAUDE_CODE_USE_BEDROCK") == "1"
        and "model id" in error_message_lower
    ):
        switch = "--model" if _is_non_interactive() else "/model"
        fallback = _get_3p_model_fallback_suggestion(model)
        if fallback:
            return create_assistant_api_error_message(
                content=(
                    f"{API_ERROR_MESSAGE_PREFIX} ({model}): {error_message}. "
                    f"Try {switch} to switch to {fallback}."
                ),
                error="invalid_request",
            )
        return create_assistant_api_error_message(
            content=(
                f"{API_ERROR_MESSAGE_PREFIX} ({model}): {error_message}. "
                f"Run {switch} to pick a different model."
            ),
            error="invalid_request",
        )

    # 24. 404 model not found
    if _get_status_code(error) == 404:
        switch = "--model" if _is_non_interactive() else "/model"
        fallback = _get_3p_model_fallback_suggestion(model)
        if fallback:
            return create_assistant_api_error_message(
                content=(
                    f"The model {model} is not available. "
                    f"Try {switch} to switch to {fallback}, "
                    f"or ask your admin to enable this model."
                ),
                error="invalid_request",
            )
        return create_assistant_api_error_message(
            content=(
                f"There's an issue with the selected model ({model}). "
                f"It may not exist or you may not have access to it. "
                f"Run {switch} to pick a different model."
            ),
            error="invalid_request",
        )

    # 25. Connection errors (non-timeout)
    if _is_connection_error(error):
        from hare.services.api.error_utils import format_api_error
        return create_assistant_api_error_message(
            content=f"{API_ERROR_MESSAGE_PREFIX}: {format_api_error(error)}",
            error="unknown",
        )

    # 26. Generic Error with message
    if isinstance(error, Exception):
        return create_assistant_api_error_message(
            content=f"{API_ERROR_MESSAGE_PREFIX}: {error_message}",
            error="unknown",
        )

    # 27. Unknown — bare fallback
    return create_assistant_api_error_message(
        content=API_ERROR_MESSAGE_PREFIX,
        error="unknown",
    )


# ---------------------------------------------------------------------------
# Error classification helpers
# ---------------------------------------------------------------------------


def _get_status_code(error: Any) -> int:
    """Extract HTTP status code from various error types."""
    if isinstance(error, APIError):
        return error.status_code
    # Try SDK error types
    if hasattr(error, "status_code"):
        return error.status_code
    if hasattr(error, "status"):
        return error.status
    return 0


def _is_timeout_error(error: Any) -> bool:
    """Check if error is a timeout-related error."""
    msg = str(error).lower()
    if "timeout" in msg:
        # Check for known timeout error types
        if isinstance(error, Exception):
            error_name = type(error).__name__
            if "Timeout" in error_name or "TimeoutError" in error_name:
                return True
            if "APIConnection" in error_name and "timeout" in msg:
                return True
        return False
    return False


def _is_image_size_error(error: Any) -> bool:
    """Check if error is an image size/resize validation error."""
    error_name = type(error).__name__
    return error_name in ("ImageSizeError", "ImageResizeError")


def _is_connection_error(error: Any) -> bool:
    """Check if error is a connection error (non-timeout)."""
    error_name = type(error).__name__
    return "Connection" in error_name and "Timeout" not in error_name


def _is_opus_model(model: str) -> bool:
    """Check if a model string is a Claude Opus variant."""
    m = model.lower()
    return "opus" in m and not any(
        x in m for x in ("sonnet", "haiku")
    )


def _handle_429_error(error: Any, error_message: str) -> Any:
    """Handle 429 rate limit errors with subscriber-aware messaging."""
    from hare.utils.messages import create_assistant_api_error_message

    # Try to get rate limit headers
    rate_limit_type = _get_header(error, "anthropic-ratelimit-unified-representative-claim")
    overage_status = _get_header(error, "anthropic-ratelimit-unified-overage-status")

    if rate_limit_type or overage_status:
        # New API with unified rate limit headers — simplified message
        if rate_limit_type:
            return create_assistant_api_error_message(
                content=f"Rate limit reached ({rate_limit_type}). Please wait and try again.",
                error="rate_limit",
            )
        return create_assistant_api_error_message(
            content=f"Rate limit reached. Please wait and try again.",
            error="rate_limit",
        )

    # No quota headers — surface what the API actually said
    if "Extra usage is required for long context" in error_message:
        hint = (
            "enable extra usage at claude.ai/settings/usage, or use --model to switch to standard context"
            if _is_non_interactive()
            else "run /extra-usage to enable, or /model to switch to standard context"
        )
        return create_assistant_api_error_message(
            content=f"{API_ERROR_MESSAGE_PREFIX}: Extra usage is required for 1M context · {hint}",
            error="rate_limit",
        )

    # Extract inner message from SDK's JSON-wrapped error
    stripped = re.sub(r"^429\s+", "", error_message)
    inner_match = re.search(r'"message"\s*:\s*"([^"]*)"', stripped)
    detail = inner_match.group(1) if inner_match else stripped
    return create_assistant_api_error_message(
        content=(
            f"{API_ERROR_MESSAGE_PREFIX}: Request rejected (429) · "
            f"{detail or 'this may be a temporary capacity issue — check status.anthropic.com'}"
        ),
        error="rate_limit",
    )


def _get_header(error: Any, name: str) -> Optional[str]:
    """Extract a named header from an error object."""
    headers = getattr(error, "headers", None)
    if isinstance(headers, dict):
        return headers.get(name)
    return None


# ---------------------------------------------------------------------------
# classify_api_error — taxonomy for analytics
# ---------------------------------------------------------------------------


def classify_api_error(error: Any) -> str:
    """Classify API error into a standardized taxonomy string for analytics.

    Returns one of:
      aborted, api_timeout, repeated_529, capacity_off_switch,
      rate_limit, server_overload, prompt_too_long,
      pdf_too_large, pdf_password_protected, image_too_large,
      tool_use_mismatch, unexpected_tool_result, duplicate_tool_use_id,
      invalid_model, credit_balance_low, invalid_api_key,
      token_revoked, oauth_org_not_allowed, auth_error,
      bedrock_model_access, server_error, client_error,
      ssl_cert_error, connection_error, unknown
    """
    error_message = str(error) if error else ""
    error_message_lower = error_message.lower()
    status = _get_status_code(error)

    # Aborted requests
    if error_message == "Request was aborted.":
        return "aborted"

    # Timeout errors
    if _is_timeout_error(error):
        return "api_timeout"

    # Repeated 529
    if REPEATED_529_ERROR_MESSAGE in error_message:
        return "repeated_529"

    # Capacity off-switch
    if CUSTOM_OFF_SWITCH_MESSAGE in error_message:
        return "capacity_off_switch"

    # Rate limiting (429)
    if status == 429:
        return "rate_limit"

    # Server overload (529)
    if status == 529 or '"type":"overloaded_error"' in str(error_message):
        return "server_overload"

    # Prompt too long
    if PROMPT_TOO_LONG_ERROR_MESSAGE.lower() in error_message_lower:
        return "prompt_too_long"

    # PDF errors
    if re.search(r"maximum of \d+ PDF pages", error_message):
        return "pdf_too_large"
    if "The PDF specified is password protected" in error_message:
        return "pdf_password_protected"

    # Image size errors
    if (
        status == 400
        and "image exceeds" in error_message
        and "maximum" in error_message
    ):
        return "image_too_large"
    if (
        status == 400
        and "image dimensions exceed" in error_message
        and "many-image" in error_message
    ):
        return "image_too_large"

    # Tool use errors (400)
    if (
        status == 400
        and "tool_use` ids were found without `tool_result` blocks immediately after"
        in error_message
    ):
        return "tool_use_mismatch"
    if (
        status == 400
        and "unexpected `tool_use_id` found in `tool_result`" in error_message
    ):
        return "unexpected_tool_result"
    if (
        status == 400
        and "tool_use` ids must be unique" in error_message
    ):
        return "duplicate_tool_use_id"

    # Invalid model errors (400)
    if status == 400 and "invalid model name" in error_message_lower:
        return "invalid_model"

    # Credit/billing errors
    if CREDIT_BALANCE_TOO_LOW_ERROR_MESSAGE.lower() in error_message_lower:
        return "credit_balance_low"

    # Authentication errors
    if "x-api-key" in error_message_lower:
        return "invalid_api_key"
    if status == 403 and "OAuth token has been revoked" in error_message:
        return "token_revoked"
    if (
        status in (401, 403)
        and "OAuth authentication is currently not allowed for this organization"
        in error_message
    ):
        return "oauth_org_not_allowed"
    if status in (401, 403):
        return "auth_error"

    # Bedrock model access
    if (
        os.environ.get("CLAUDE_CODE_USE_BEDROCK") == "1"
        and "model id" in error_message_lower
    ):
        return "bedrock_model_access"

    # Status code based fallbacks
    if status >= 500:
        return "server_error"
    if status >= 400:
        return "client_error"

    # Connection errors with SSL detection
    if _is_connection_error(error):
        try:
            from hare.services.api.error_utils import extract_connection_error_details
            details = extract_connection_error_details(error)
            if details and details.get("is_ssl_error"):
                return "ssl_cert_error"
        except Exception:
            pass
        return "connection_error"

    return "unknown"


# ---------------------------------------------------------------------------
# categorize_retryable_api_error — for SDK agent use
# ---------------------------------------------------------------------------


def categorize_retryable_api_error(error: Any) -> str:
    """Categorize APIError for SDK agent error reporting.

    Returns one of: rate_limit, authentication_failed, server_error, unknown.
    """
    status = _get_status_code(error)
    error_message = str(error) if error else ""

    if status == 529 or '"type":"overloaded_error"' in error_message:
        return "rate_limit"
    if status == 429:
        return "rate_limit"
    if status in (401, 403):
        return "authentication_failed"
    if status >= 408:
        return "server_error"
    return "unknown"


# ---------------------------------------------------------------------------
# get_error_message_if_refusal
# ---------------------------------------------------------------------------


def get_error_message_if_refusal(
    stop_reason: Optional[str],
    model: str = "",
) -> Any:
    """Create an error message if the model refused to answer (stop_reason == 'refusal')."""
    if stop_reason != "refusal":
        return None

    from hare.utils.messages import create_assistant_api_error_message

    return create_assistant_api_error_message(
        content=(
            "Claude refused to respond to your prompt. "
            "This may be due to our Usage Policy "
            "(https://docs.anthropic.com/en/docs/resources/usage-policy). "
            "Please reformulate your request."
        ),
        error="invalid_request",
    )


# ---------------------------------------------------------------------------
# Retryable check
# ---------------------------------------------------------------------------


def is_retryable_api_error(error: Exception) -> bool:
    """Check if an error should be retried."""
    if isinstance(error, RateLimitError):
        return True
    if isinstance(error, OverloadedError):
        return True
    if isinstance(error, APIError) and error.status_code in (408, 409, 502, 503):
        return True
    msg = str(error).lower()
    return any(
        kw in msg for kw in ("overloaded", "rate limit", "timeout", "connection")
    )


def extract_error_message(error: Exception) -> str:
    """Extract a clean error message from an exception."""
    if isinstance(error, APIError):
        return str(error)
    return str(error)
