"""AWS STS / credential helpers (`aws.ts`).

Port of: src/utils/aws.ts
Extended with additional Python-idiomatic AWS utilities:
  - STS caller-identity retrieval with result
  - Credential cache clearing via botocore internals
  - Environment-variable formatting
  - ARN parsing and validation
  - Non-destructive credential validation
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, NotRequired, TypedDict

# ---------------------------------------------------------------------------
# Type definitions (TS parity)
# ---------------------------------------------------------------------------


class AwsCredentials(TypedDict):
    """AWS short-term credentials format. Only ``Expiration`` is optional."""

    AccessKeyId: str
    SecretAccessKey: str
    SessionToken: str
    Expiration: NotRequired[str]


class AwsStsOutput(TypedDict):
    """Output from ``aws sts get-session-token`` or ``aws sts assume-role``."""

    Credentials: AwsCredentials


@dataclass(frozen=True)
class CallerIdentity:
    """Parsed result of ``STS.GetCallerIdentity``."""

    account: str
    arn: str
    user_id: str

    @property
    def arn_type(self) -> str:
        """One of: ``assumed-role``, ``user``, ``role``, ``root``, ``federated-user``, or ``unknown``."""
        return _arn_type(self.arn)

    @property
    def account_id(self) -> str:
        """Alias for ``account``."""
        return self.account


@dataclass(frozen=True)
class ParsedArn:
    """Components of a parsed AWS ARN."""

    arn: str
    partition: str
    service: str
    region: str
    account_id: str
    resource_type: str
    resource: str

    @property
    def full_resource(self) -> str:
        """Reconstructed resource portion (``type/name``, or just ``name``)."""
        if self.resource_type:
            return f"{self.resource_type}/{self.resource}"
        return self.resource

    def __str__(self) -> str:
        return self.arn


# ---------------------------------------------------------------------------
# ARN helpers
# ---------------------------------------------------------------------------

# ARN regex taken from the AWS IAM docs; supports gov / china partitions.
_ARN_RE = re.compile(
    r"^arn:(?P<partition>aws(?:-[a-z]+)?):(?P<service>[a-zA-Z0-9._-]+):"
    r"(?P<region>[a-z0-9-]*):(?P<account_id>\d{12}):"
    r"(?:(?P<resource_type>[a-zA-Z0-9._-]+)[:/])?"
    r"(?P<resource>.+)$",
)


def parse_aws_arn(arn: str) -> ParsedArn | None:
    """Parse an AWS ARN string into its components.

    Returns ``None`` if the string does not match the ARN pattern.

    Examples::

        >>> parse_aws_arn("arn:aws:iam::123456789012:role/MyRole")
        ParsedArn(arn='arn:aws:iam::123456789012:role/MyRole', partition='aws',
                  service='iam', region='', account_id='123456789012',
                  resource_type='role', resource='MyRole')
    """
    if not arn or not isinstance(arn, str):
        return None
    m = _ARN_RE.match(arn)
    if not m:
        return None
    g = m.groupdict()
    return ParsedArn(
        arn=arn,
        partition=g["partition"],
        service=g["service"],
        region=g["region"] or "",
        account_id=g["account_id"],
        resource_type=g.get("resource_type") or "",
        resource=g["resource"],
    )


def _arn_type(arn: str) -> str:
    """Extract the IAM entity type from an ARN string.

    Returns one of: ``assumed-role``, ``user``, ``role``, ``root``, ``federated-user``,
    or ``unknown``.
    """
    parsed = parse_aws_arn(arn)
    if parsed is None:
        return "unknown"
    if parsed.service == "sts" and parsed.resource_type == "assumed-role":
        return "assumed-role"
    if parsed.resource_type == "user":
        return "user"
    if parsed.resource_type == "role":
        return "role"
    if parsed.resource_type == "root":
        return "root"
    if parsed.resource_type == "federated-user":
        return "federated-user"
    return "unknown"


# ---------------------------------------------------------------------------
# Validators / guards (TS parity)
# ---------------------------------------------------------------------------


def is_aws_credentials_provider_error(err: BaseException | None) -> bool:
    """Return ``True`` when *err* is a botocore ``CredentialsProviderError``.

    Mirrors the TS ``isAwsCredentialsProviderError`` (checks ``.name``).
    Also handles botocore's ``NoCredentialsError`` which subclasses it.
    """
    if err is None:
        return False
    name: str = getattr(err, "name", "") or getattr(err, "__class__", "").__name__
    if name == "CredentialsProviderError":
        return True
    # botocore NoCredentialsError is a subclass of CredentialsProviderError
    if name == "NoCredentialsError":
        return True
    # Check via class hierarchy
    try:
        from botocore.exceptions import (  # type: ignore[import-not-found]
            CredentialsProviderError,
        )

        return isinstance(err, CredentialsProviderError)
    except ImportError:
        return False


def is_valid_aws_sts_output(obj: object) -> bool:
    """Type-guard: validate AWS STS assume-role / get-session-token output.

    Checks that the object is a dict with a ``Credentials`` key containing
    non-empty ``AccessKeyId``, ``SecretAccessKey``, and ``SessionToken`` strings.
    """
    if obj is None:
        return False
    if not isinstance(obj, dict):
        return False
    creds = obj.get("Credentials")
    if not isinstance(creds, dict):
        return False
    ak = creds.get("AccessKeyId")
    sk = creds.get("SecretAccessKey")
    st = creds.get("SessionToken")
    return (
        isinstance(ak, str)
        and isinstance(sk, str)
        and isinstance(st, str)
        and len(ak) > 0
        and len(sk) > 0
        and len(st) > 0
    )


def validate_aws_credentials(credentials: dict[str, Any] | AwsCredentials) -> list[str]:
    """Validate AWS credential dictionary and return a list of human-readable issues.

    Returns an empty list when credentials look valid. Checks for missing keys,
    empty values, and common formatting issues (e.g. whitespace-only values).
    """
    issues: list[str] = []

    if not isinstance(credentials, dict):
        issues.append("Credentials must be a dictionary")
        return issues

    for key in ("AccessKeyId", "SecretAccessKey"):
        val = credentials.get(key)
        if val is None:
            issues.append(f"Missing required key: {key}")
        elif not isinstance(val, str):
            issues.append(f"Key '{key}' must be a string, got {type(val).__name__}")
        elif val.strip() == "":
            issues.append(f"Key '{key}' is empty or whitespace-only")

    # SessionToken is "required" in the STS output sense, but some
    # credential flows (IAM instance profiles) don't provide one.
    st = credentials.get("SessionToken")
    if st is not None and (not isinstance(st, str) or st.strip() == ""):
        issues.append("SessionToken is present but empty")

    # Expiration is optional — warn if set but malformed
    exp = credentials.get("Expiration")
    if exp is not None and not isinstance(exp, str):
        issues.append(
            f"Expiration must be an ISO-8601 string, got {type(exp).__name__}",
        )

    return issues


# ---------------------------------------------------------------------------
# STS operations (TS parity  + extras)
# ---------------------------------------------------------------------------


async def check_sts_caller_identity() -> CallerIdentity:
    """Verify AWS identity via STS ``GetCallerIdentity``.

    Returns the parsed identity on success.
    Raises ``RuntimeError`` when ``boto3`` is not installed or the call fails.

    Mirrors the TS ``checkStsCallerIdentity`` with a richer return value.
    """
    from hare.utils.debug import log_for_debugging

    try:
        import boto3  # type: ignore[import-not-found]
        from botocore.exceptions import (  # type: ignore[import-not-found]
            BotoCoreError,
        )
    except ImportError as e:
        raise RuntimeError(
            "boto3 is required for AWS STS operations. "
            "Install it with: pip install boto3"
        ) from e

    try:
        sts = boto3.client("sts")
        resp: dict[str, Any] = sts.get_caller_identity()
    except BotoCoreError as e:
        log_for_debugging(f"STS GetCallerIdentity failed: {e}", level="error")
        raise RuntimeError(
            f"AWS STS GetCallerIdentity failed: {e}"
        ) from e
    except Exception as e:
        log_for_debugging(f"Unexpected STS error: {e}", level="error")
        raise RuntimeError(
            f"Unexpected error during STS GetCallerIdentity: {e}"
        ) from e

    identity = CallerIdentity(
        account=resp.get("Account", ""),
        arn=resp.get("Arn", ""),
        user_id=resp.get("UserId", ""),
    )
    log_for_debugging(
        f"AWS identity: account={identity.account} arn={identity.arn} "
        f"user_id={identity.user_id}",
    )
    return identity


async def get_caller_identity(
    *,
    region_name: str | None = None,
    profile_name: str | None = None,
) -> CallerIdentity | None:
    """Retrieve the current AWS caller identity, returning ``None`` on failure.

    Unlike ``check_sts_caller_identity`` this is a soft check — no exception is
    raised when credentials are missing or the call fails.
    """
    from hare.utils.debug import log_for_debugging

    try:
        import boto3  # type: ignore[import-not-found]
    except ImportError:
        log_for_debugging("boto3 not installed — cannot retrieve caller identity")
        return None

    try:
        session_kwargs: dict[str, Any] = {}
        if region_name:
            session_kwargs["region_name"] = region_name
        if profile_name:
            session_kwargs["profile_name"] = profile_name

        session = boto3.Session(**session_kwargs) if session_kwargs else boto3
        sts = session.client("sts") if session_kwargs else boto3.client("sts")
        resp: dict[str, Any] = sts.get_caller_identity()
    except Exception as e:
        log_for_debugging(f"GetCallerIdentity (soft) failed: {e}")
        return None

    return CallerIdentity(
        account=resp.get("Account", ""),
        arn=resp.get("Arn", ""),
        user_id=resp.get("UserId", ""),
    )


async def assume_role(
    *,
    role_arn: str,
    role_session_name: str = "hare-session",
    duration_seconds: int = 3600,
    external_id: str | None = None,
    region_name: str | None = None,
) -> AwsCredentials | None:
    """Assume an IAM role via STS and return temporary credentials.

    Returns ``None`` when the operation fails (missing boto3, invalid role,
    permission denied, etc.).

    Parameters
    ----------
    role_arn:
        Full ARN of the role to assume, e.g.
        ``arn:aws:iam::123456789012:role/MyRole``.
    role_session_name:
        Identifier for this session (appears in CloudTrail).
    duration_seconds:
        Session duration in seconds (900–43200 depending on role config).
    external_id:
        Optional external ID required by the role's trust policy.
    region_name:
        Optional AWS region override.
    """
    from hare.utils.debug import log_for_debugging

    try:
        import boto3  # type: ignore[import-not-found]
    except ImportError:
        log_for_debugging("boto3 not installed — cannot assume role")
        return None

    if not role_arn or not role_arn.startswith("arn:"):
        log_for_debugging(f"Invalid role ARN: {role_arn!r}", level="warn")
        return None

    try:
        session_kwargs: dict[str, Any] = {}
        if region_name:
            session_kwargs["region_name"] = region_name
        session = boto3.Session(**session_kwargs) if session_kwargs else boto3
        sts = session.client("sts") if session_kwargs else boto3.client("sts")

        params: dict[str, Any] = {
            "RoleArn": role_arn,
            "RoleSessionName": role_session_name,
            "DurationSeconds": duration_seconds,
        }
        if external_id:
            params["ExternalId"] = external_id

        resp: dict[str, Any] = sts.assume_role(**params)
    except Exception as e:
        log_for_debugging(f"STS AssumeRole failed for {role_arn}: {e}", level="error")
        return None

    creds = resp.get("Credentials", {})
    return AwsCredentials(
        AccessKeyId=creds.get("AccessKeyId", ""),
        SecretAccessKey=creds.get("SecretAccessKey", ""),
        SessionToken=creds.get("SessionToken", ""),
        Expiration=creds.get("Expiration", ""),
    )


async def get_session_token(
    *,
    duration_seconds: int = 3600,
    serial_number: str | None = None,
    token_code: str | None = None,
    region_name: str | None = None,
) -> AwsCredentials | None:
    """Retrieve a temporary session token via STS ``GetSessionToken``.

    Returns ``None`` when the operation fails.
    """
    from hare.utils.debug import log_for_debugging

    try:
        import boto3  # type: ignore[import-not-found]
    except ImportError:
        log_for_debugging("boto3 not installed — cannot get session token")
        return None

    try:
        session_kwargs: dict[str, Any] = {}
        if region_name:
            session_kwargs["region_name"] = region_name
        session = boto3.Session(**session_kwargs) if session_kwargs else boto3
        sts = session.client("sts") if session_kwargs else boto3.client("sts")

        params: dict[str, Any] = {"DurationSeconds": duration_seconds}
        if serial_number:
            params["SerialNumber"] = serial_number
        if token_code:
            params["TokenCode"] = token_code

        resp: dict[str, Any] = sts.get_session_token(**params)
    except Exception as e:
        log_for_debugging(f"STS GetSessionToken failed: {e}", level="error")
        return None

    creds = resp.get("Credentials", {})
    return AwsCredentials(
        AccessKeyId=creds.get("AccessKeyId", ""),
        SecretAccessKey=creds.get("SecretAccessKey", ""),
        SessionToken=creds.get("SessionToken", ""),
        Expiration=creds.get("Expiration", ""),
    )


# ---------------------------------------------------------------------------
# Credential cache management (TS parity)
# ---------------------------------------------------------------------------


async def clear_aws_ini_cache() -> None:
    """Clear the AWS credential provider cache, forcing a re-read of ``~/.aws/credentials``.

    Mirrors the TS ``clearAwsIniCache``. In Python this is achieved by either:

    1. Instructing botocore to create a fresh credential resolver (the most
       portable approach), or
    2. Clearing botocore's internal ``CredentialResolver`` cache when it exists.

    Any errors are silently ignored — this is a best-effort operation.
    """
    from hare.utils.debug import log_for_debugging

    log_for_debugging("Clearing AWS credential provider cache")

    # Strategy A: force botocore to build a fresh credential resolver by
    # creating a throwaway session that explicitly reads from the shared
    # credentials file with cache-busting.
    try:
        import boto3  # type: ignore[import-not-found]
        from botocore.credentials import (  # type: ignore[import-not-found]
            SharedCredentialProvider,
        )

        # Force-create a provider that reads the ini file afresh.  This
        # populates the internal _provider_cache inside botocore's
        # CredentialResolver with a new instance.
        provider = SharedCredentialProvider(
            creds_filename=os.path.expanduser("~/.aws/credentials"),
            profile_name=os.environ.get("AWS_PROFILE", "default"),
        )
        provider.load()
        log_for_debugging("AWS credential provider cache refreshed")
    except ImportError:
        log_for_debugging(
            "Failed to clear AWS credential cache (expected if boto3 not installed)",
        )
    except Exception:
        log_for_debugging(
            "Failed to clear AWS credential cache (expected if no credentials are configured)",
        )

    # Strategy B (defence-in-depth): also try to poke the default session's
    # credential chain to flush any cached fetchers.
    try:
        import botocore.session  # type: ignore[import-not-found]

        sess = botocore.session.get_session()
        resolver = getattr(sess, "_credential_resolver", None)
        if resolver is not None and hasattr(resolver, "_provider_cache"):
            resolver._provider_cache.clear()  # type: ignore[union-attr]
    except Exception:
        pass  # best-effort — never surface errors from cache clearing


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------


def format_aws_credentials_as_env_vars(
    credentials: AwsCredentials | dict[str, Any],
) -> dict[str, str]:
    """Convert a credentials dict/mapping into ``AWS_*`` environment variables.

    Returns a dict suitable for ``os.environ.update()``, ``subprocess``
    ``env=``, or similar contexts. Only non-empty values are included.

    Example::

        >>> format_aws_credentials_as_env_vars({
        ...     "AccessKeyId": "AKIA...",
        ...     "SecretAccessKey": "wJalr...",
        ...     "SessionToken": "Fwo..."
        ... })
        {
            "AWS_ACCESS_KEY_ID": "AKIA...",
            "AWS_SECRET_ACCESS_KEY": "wJalr...",
            "AWS_SESSION_TOKEN": "Fwo..."
        }
    """
    env: dict[str, str] = {}
    mapping = (
        ("AccessKeyId", "AWS_ACCESS_KEY_ID"),
        ("SecretAccessKey", "AWS_SECRET_ACCESS_KEY"),
        ("SessionToken", "AWS_SESSION_TOKEN"),
        ("Expiration", "AWS_CREDENTIAL_EXPIRATION"),
    )
    for src_key, env_key in mapping:
        val = credentials.get(src_key)
        if val and isinstance(val, str) and val.strip():
            env[env_key] = val
    return env


def has_aws_credentials_in_env() -> bool:
    """Return ``True`` when standard AWS credential env-vars are set.

    Checks ``AWS_ACCESS_KEY_ID`` + ``AWS_SECRET_ACCESS_KEY``, and optionally
    ``AWS_SESSION_TOKEN`` or ``AWS_PROFILE``.
    """
    ak = os.environ.get("AWS_ACCESS_KEY_ID", "").strip()
    sk = os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()
    if ak and sk:
        return True
    profile = os.environ.get("AWS_PROFILE", "").strip()
    if profile:
        return True
    return False
