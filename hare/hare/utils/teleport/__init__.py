"""Teleport API utilities."""

from hare.utils.teleport.api import (
    # Auth
    prepare_api_request,
    get_oauth_headers,
    # Session CRUD
    fetch_session,
    fetch_code_sessions,
    send_event_to_remote_session,
    update_session_title,
    # Branch extraction
    get_branch_from_session,
    # Retry helpers
    is_transient_network_error,
    # Injectables
    _set_injectables,
    # Error classes
    TeleportAuthError,
    TeleportApiError,
    # Types
    CCR_BYOC_BETA,
    SessionResource,
    SessionContext,
    CodeSession,
    RepoInfo,
    RepoOwner,
    ListSessionsResponse,
    GitSource,
    KnowledgeBaseSource,
    OutcomeGitInfo,
    GitRepositoryOutcome,
    # Type aliases
    RemoteMessageContent,
    SessionStatus,
    CodeSessionStatus,
    PrepareApiRequestFn,
    GetOAuthConfigFn,
    GetClaudeAIOAuthTokensFn,
    GetOrganizationUUIDFn,
)
