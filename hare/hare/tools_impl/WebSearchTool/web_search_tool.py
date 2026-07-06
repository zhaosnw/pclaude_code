"""
WebSearchTool - web search via Anthropic beta API.

Port of: src/tools/WebSearchTool/WebSearchTool.ts

Supports streaming search result processing, domain filtering,
input validation, and markdown output formatting.
"""

from __future__ import annotations

import time
from typing import Any

TOOL_NAME = "WebSearch"
WEB_SEARCH_TOOL_NAME = TOOL_NAME

MIN_QUERY_LENGTH = 2


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query to use", "minLength": MIN_QUERY_LENGTH},
            "allowed_domains": {"type": "array", "items": {"type": "string"},
                               "description": "Only include search results from these domains"},
            "blocked_domains": {"type": "array", "items": {"type": "string"},
                               "description": "Never include search results from these domains"},
        },
        "required": ["query"],
    }


def make_tool_schema(input: dict[str, Any]) -> dict[str, Any]:
    """Build the web_search_20250305 server-side tool schema."""
    return {
        "type": "function",
        "function": {
            "name": "web_search_20250305",
            "description": "Search the web. Returns result blocks with titles and URLs. US-only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query to use", "minLength": MIN_QUERY_LENGTH},
                    "allowed_domains": {"type": "array", "items": {"type": "string"},
                                       "description": "Only include results from these domains"},
                    "blocked_domains": {"type": "array", "items": {"type": "string"},
                                       "description": "Never include results from these domains"},
                },
                "required": ["query"],
            },
        },
    }


def make_output_from_search_response(results: list[dict[str, Any]], query: str, duration: float) -> dict[str, Any]:
    """Parse streaming search blocks into structured output."""
    parsed = []
    for block in results:
        if not isinstance(block, dict):
            continue
        parsed.append({"title": str(block.get("title", "") or ""),
                       "url": str(block.get("url", "") or ""),
                       "snippet": str(block.get("snippet", "") or "")})
    return {"query": query, "results": parsed, "durationSeconds": duration}


def format_search_results(output: dict[str, Any]) -> str:
    """Format search results as markdown with sources reminder."""
    query = output.get("query", "")
    results = output.get("results", [])
    if not results:
        return f"No results found for: '{query}'."

    lines = [f"Web search results for: '{query}'\n"]
    for idx, r in enumerate(results, start=1):
        title = r.get("title", "").strip()
        url = r.get("url", "").strip()
        snippet = r.get("snippet", "").strip()
        lines.append(f"**{idx}. {title}**")
        lines.append(f"   {url}")
        if snippet:
            lines.append(f"   {snippet}")
        lines.append("")

    dur = output.get("durationSeconds", 0)
    if dur:
        lines.append(f"*Search completed in {dur:.2f}s*")
    lines.append("*Sources: review each result and cite URLs used.*")
    return "\n".join(lines)


def _validate_query(query: str) -> str | None:
    if not query or not query.strip():
        return "Query must not be empty."
    if len(query.strip()) < MIN_QUERY_LENGTH:
        return f"Query must be at least {MIN_QUERY_LENGTH} characters."
    return None


def _validate_domains(domains: list[str] | None, label: str) -> list[str]:
    errors: list[str] = []
    if domains is None:
        return errors
    for d in domains:
        ds = d.strip()
        if "://" in ds:
            errors.append(f"{label}: Domain '{ds}' should not contain a scheme.")
        if "/" in ds:
            errors.append(f"{label}: Domain '{ds}' should not contain a path.")
    return errors


async def call(query: str, allowed_domains: list[str] | None = None,
               blocked_domains: list[str] | None = None, **kwargs: Any) -> dict[str, Any]:
    start = time.time()

    query_err = _validate_query(query)
    if query_err:
        return {"query": query, "results": [], "durationSeconds": 0.0, "error": query_err}

    val_errs = [*_validate_domains(allowed_domains, "allowed_domains"),
                *_validate_domains(blocked_domains, "blocked_domains")]
    if val_errs:
        return {"query": query, "results": [], "durationSeconds": 0.0, "error": "; ".join(val_errs)}

    from hare.services.api import create_api_client

    client = create_api_client()
    try:
        result = await client.web_search(query=query, allowed_domains=allowed_domains,
                                          blocked_domains=blocked_domains)
        duration = time.time() - start
        return make_output_from_search_response(result.get("results", []), query, duration)
    except Exception as e:
        duration = time.time() - start
        error_msg = str(e)
        if "429" in error_msg or "rate limit" in error_msg.lower():
            return {"query": query, "results": [], "durationSeconds": duration,
                    "error": f"Rate limit exceeded. Please wait before retrying. {error_msg}"}
        return {"query": query, "results": [], "durationSeconds": duration, "error": f"Search failed: {error_msg}"}
