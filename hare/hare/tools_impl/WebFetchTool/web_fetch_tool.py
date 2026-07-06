"""
WebFetchTool - fetch URL content and process with AI.

Port of: src/tools/WebFetchTool/WebFetchTool.ts

Fetches content from URLs, converts HTML to markdown, supports
AI-based content processing via secondary model prompt, includes
15-minute TTL cache and preapproved-domain security checks.
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

from hare.tools_impl.WebFetchTool.prompt import make_secondary_model_prompt

TOOL_NAME = "WebFetch"
WEB_FETCH_TOOL_NAME = TOOL_NAME

PREAPPROVED_DOMAINS = frozenset([
    "github.com", "docs.anthropic.com", "pypi.org", "npmjs.com",
    "crates.io", "docs.rs", "stackoverflow.com", "wikipedia.org",
    "docs.python.org", "developer.mozilla.org",
])

CACHE_TTL_SECONDS = 15 * 60
MAX_CONTENT_LENGTH = 50_000
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; Hare/2.1; +https://claude.com/claude-code)"
MAX_REDIRECTS = 5
FETCH_TIMEOUT = 30

# HTML-to-markdown regexes
_RE_SCRIPT_STYLE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_RE_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_RE_HEAD = re.compile(r"</?head[^>]*>", re.IGNORECASE)
_RE_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_RE_HR = re.compile(r"<hr\s*/?>", re.IGNORECASE)
_RE_P = re.compile(r"</?p[^>]*>", re.IGNORECASE)
_RE_DIV = re.compile(r"</?div[^>]*>", re.IGNORECASE)
_RE_H = re.compile(r"</?h([1-6])[^>]*>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)
_RE_A = re.compile(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_RE_IMG = re.compile(r'<img\s[^>]*src=["\']([^"\']+)["\'][^>]*/?>', re.IGNORECASE)
_RE_STRONG = re.compile(r"</?(?:strong|b)>", re.IGNORECASE)
_RE_EM = re.compile(r"</?(?:em|i)>", re.IGNORECASE)
_RE_CODE_INLINE = re.compile(r"</?code>", re.IGNORECASE)
_RE_LI = re.compile(r"<li[^>]*>(.*?)</li>", re.IGNORECASE | re.DOTALL)
_RE_UL = re.compile(r"<ul[^>]*>(.*?)</ul>", re.IGNORECASE | re.DOTALL)
_RE_OL = re.compile(r"<ol[^>]*>(.*?)</ol>", re.IGNORECASE | re.DOTALL)
_RE_PRE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.IGNORECASE | re.DOTALL)
_RE_BLOCKQUOTE = re.compile(r"<blockquote[^>]*>(.*?)</blockquote>", re.IGNORECASE | re.DOTALL)
_RE_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_RE_TH_TD = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.IGNORECASE | re.DOTALL)
_RE_REMAINING_TAG = re.compile(r"<[^>]+>")


@dataclass
class _CacheEntry:
    content: str
    status_code: int
    content_type: str
    byte_length: int
    timestamp: float = field(default_factory=time.time)

_url_cache: dict[str, _CacheEntry] = {}


def _cache_get(url: str) -> Optional[_CacheEntry]:
    entry = _url_cache.get(url)
    if entry is None:
        return None
    if time.time() - entry.timestamp > CACHE_TTL_SECONDS:
        _url_cache.pop(url, None)
        return None
    return entry


def _cache_set(url: str, content: str, status_code: int = 200,
               content_type: str = "text/html", byte_length: int = 0) -> None:
    _url_cache[url] = _CacheEntry(content=content, status_code=status_code,
                                   content_type=content_type, byte_length=byte_length)


def is_preapproved_url(url: str) -> bool:
    host = urlparse(url).hostname or ""
    host_lower = host.lower()
    for domain in PREAPPROVED_DOMAINS:
        if host_lower == domain or host_lower.endswith("." + domain):
            return True
    return False


def _sanitize_url(url: str) -> str:
    if url.startswith("http://"):
        url = "https://" + url[7:]
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        host = parsed.hostname or ""
        if parsed.port:
            host += f":{parsed.port}"
        url = f"{parsed.scheme}://{host}{parsed.path}"
        if parsed.query:
            url += f"?{parsed.query}"
    return url


async def _fetch_url_content(url: str) -> dict[str, Any]:
    current_url = url
    for _ in range(MAX_REDIRECTS):
        req = urllib.request.Request(current_url, headers={"User-Agent": DEFAULT_USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
                final_url = resp.geturl() or current_url
                content_type = resp.headers.get("Content-Type", "text/html")
                raw_bytes = resp.read()
                try:
                    content = raw_bytes.decode("utf-8", errors="replace")
                except Exception:
                    content = raw_bytes.decode("latin-1", errors="replace")
                return {"bytes": len(raw_bytes), "code": resp.getcode() or 200,
                        "codeText": _status_text(resp.getcode() or 200),
                        "content": content, "contentType": content_type, "url": final_url}
        except urllib.error.HTTPError as e:
            return {"bytes": 0, "code": e.code, "codeText": f"HTTP {e.code}",
                    "content": str(e), "contentType": "text/plain", "url": current_url}
        except urllib.error.URLError as e:
            return {"bytes": 0, "code": 0, "codeText": "Connection Error",
                    "content": f"URL error: {e.reason}", "contentType": "text/plain", "url": current_url}
    return {"bytes": 0, "code": 310, "codeText": "Too Many Redirects",
            "content": "Exceeded maximum redirects.", "contentType": "text/plain", "url": current_url}


def _status_text(code: int) -> str:
    _MAP = {200: "OK", 301: "Moved", 302: "Found", 304: "Not Modified", 400: "Bad Request",
            401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 429: "Too Many Requests",
            500: "Internal Server Error", 502: "Bad Gateway", 503: "Service Unavailable"}
    return _MAP.get(code, f"HTTP {code}")


def _html_to_markdown(html: str) -> str:
    if not html or not html.strip():
        return ""
    text = html
    text = _RE_SCRIPT_STYLE.sub("", text)
    text = _RE_COMMENT.sub("", text)
    text = _RE_HEAD.sub("", text)
    text = _RE_PRE.sub(r"\n```\n\1\n```\n", text)
    text = _RE_CODE_INLINE.sub("`", text)
    text = _RE_H.sub(lambda m: f"\n{'#' * int(m.group(1))} {_strip_tags(m.group(2)).strip()}\n", text)
    text = _RE_A.sub(lambda m: f"[{_strip_tags(m.group(2)).strip()}]({m.group(1)})", text)
    text = _RE_IMG.sub(r"![](\1)", text)
    text = _RE_STRONG.sub("**", text)
    text = _RE_EM.sub("_", text)
    text = _RE_BLOCKQUOTE.sub(r"\n> \1\n", text)
    text = _RE_UL.sub(lambda m: "\n" + "".join(f"- {_strip_tags(li).strip()}\n"
                        for li in _RE_LI.findall(m.group(0))) + "\n", text)
    text = _RE_OL.sub(lambda m: "\n" + "".join(f"{i}. {_strip_tags(li).strip()}\n"
                        for i, li in enumerate(_RE_LI.findall(m.group(0)), 1)) + "\n", text)
    text = _RE_HR.sub("\n---\n", text)
    text = _RE_BR.sub("\n", text)
    text = _RE_P.sub("\n\n", text)
    text = _RE_DIV.sub("\n", text)
    text = _RE_REMAINING_TAG.sub("", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#39;", "'", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def _strip_tags(html: str) -> str:
    return _RE_REMAINING_TAG.sub("", html)


def input_schema() -> dict[str, Any]:
    return {"type": "object",
            "properties": {"url": {"type": "string", "description": "URL to fetch"},
                          "prompt": {"type": "string", "description": "What information to extract from the page"}},
            "required": ["url", "prompt"]}


async def call(url: str, prompt: str = "", **kwargs: Any) -> dict[str, Any]:
    start_time = time.time()
    url = _sanitize_url(url)

    cached = _cache_get(url)
    if cached is not None:
        elapsed = int((time.time() - start_time) * 1000)
        content = cached.content
        if prompt:
            model_prompt = make_secondary_model_prompt(content[:MAX_CONTENT_LENGTH], prompt, is_preapproved_url(url))
            return {"bytes": cached.byte_length, "code": cached.status_code,
                    "codeText": _status_text(cached.status_code), "result": model_prompt,
                    "durationMs": elapsed, "url": url}
        return {"bytes": cached.byte_length, "code": cached.status_code,
                "codeText": _status_text(cached.status_code), "result": content[:MAX_CONTENT_LENGTH],
                "durationMs": elapsed, "url": url}

    try:
        fetch_result = await _fetch_url_content(url)
    except Exception as e:
        elapsed = int((time.time() - start_time) * 1000)
        return {"bytes": 0, "code": 0, "codeText": "Fetch Error",
                "result": f"Error fetching URL: {e}", "durationMs": elapsed, "url": url}

    content = fetch_result["content"]
    content_type = fetch_result["contentType"]
    final_url = fetch_result["url"]
    status_code = fetch_result["code"]
    byte_length = fetch_result["bytes"]

    # Cross-host redirect
    if final_url != url and urlparse(final_url).hostname != urlparse(url).hostname:
        elapsed = int((time.time() - start_time) * 1000)
        return {"bytes": byte_length, "code": status_code, "codeText": _status_text(status_code),
                "result": f"The URL redirected to: {final_url}\nPlease make a new WebFetch request with this URL.",
                "durationMs": elapsed, "url": final_url}

    is_html = "html" in content_type.lower() or content.strip().startswith("<")
    markdown = _html_to_markdown(content) if is_html else content

    _cache_set(url=url, content=markdown, status_code=status_code, content_type=content_type, byte_length=byte_length)

    is_preapproved = is_preapproved_url(url)
    result_content = make_secondary_model_prompt(markdown[:MAX_CONTENT_LENGTH], prompt, is_preapproved) if prompt else markdown[:MAX_CONTENT_LENGTH]
    elapsed = int((time.time() - start_time) * 1000)
    return {"bytes": byte_length, "code": status_code, "codeText": _status_text(status_code),
            "result": result_content, "durationMs": elapsed, "url": final_url}
