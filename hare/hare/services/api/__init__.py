"""API client exports."""

from hare.services.api.client import call_model_api

__all__ = ["call_model_api", "create_api_client"]


def create_api_client(**kwargs: object) -> object:
    """Create an API client instance (P2 — stub).

    In TS this creates a configured Anthropic client with auth,
    retries, and streaming support. The Python port uses
    call_model_api() directly for now.
    """
    return call_model_api
