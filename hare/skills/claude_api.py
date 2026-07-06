"""
Claude API skill — build/optimize Claude API / Anthropic SDK apps.

Port of: src/skills/bundled/claudeApi.ts + claudeApiContent.ts (271 lines)

Includes language auto-detection from project files and multi-language content.
"""

from __future__ import annotations

import os
from typing import Any

# Supported language file extensions for auto-detection
_LANGUAGE_EXTS: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".java": "java",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".rs": "rust",
}


def _detect_language(directory: str = ".") -> str:
    """Auto-detect the primary language of a project."""
    counts: dict[str, int] = {}
    try:
        for root, _, files in os.walk(directory):
            if ".git" in root or "node_modules" in root or "__pycache__" in root:
                continue
            for f in files[:50]:  # Sample first 50 files
                ext = os.path.splitext(f)[1]
                lang = _LANGUAGE_EXTS.get(ext)
                if lang:
                    counts[lang] = counts.get(lang, 0) + 1
            if len(counts) > 2:
                break
    except OSError:
        pass
    if not counts:
        return "python"
    return max(counts, key=counts.get)


async def execute_hare_api_skill(prompt: str, context: Any = None) -> str:
    """Return guidance for building Claude API / Anthropic SDK apps.

    This skill provides specialized guidance for writing code that calls
    the Claude API or uses the Anthropic SDK.
    """
    lang = _detect_language(
        context.get("cwd", ".") if isinstance(context, dict) else "."
    )

    model_prefix = "claude-sonnet-4-6"
    if isinstance(context, dict):
        model_prefix = context.get("model", "claude-sonnet-4-6")

    content_parts = [
        "# Claude API / Anthropic SDK guidance",
        "",
        f"Detected project language: **{lang}**",
        "",
        "## Key principles",
        "- Use the latest model IDs: `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`",
        "- Enable prompt caching for cost efficiency",
        "- Use `tool_use` for structured function calling",
        "- Handle rate limits with exponential backoff",
        "",
        "## SDK quick start",
        "",
    ]

    if lang == "python":
        content_parts.extend(
            [
                "```python",
                "import anthropic",
                "",
                "client = anthropic.Anthropic()",
                "",
                "message = client.messages.create(",
                f'    model="{model_prefix}",',
                "    max_tokens=1024,",
                '    messages=[{"role": "user", "content": "Hello, Claude"}],',
                ")",
                "print(message.content)",
                "```",
            ]
        )
    elif lang == "typescript":
        content_parts.extend(
            [
                "```typescript",
                "import Anthropic from '@anthropic-ai/sdk';",
                "",
                "const anthropic = new Anthropic();",
                "",
                "const msg = await anthropic.messages.create({",
                f'  model: "{model_prefix}",',
                "  max_tokens: 1024,",
                '  messages: [{ role: "user", content: "Hello, Claude" }],',
                "});",
                "console.log(msg.content);",
                "```",
            ]
        )

    content_parts.extend(
        [
            "",
            "## Caching",
            'Set `"anthropic-beta": "prompt-caching-2024-07-31"` header for prompt caching.',
            "Cache breakpoints on system prompt content blocks for lowest cost.",
            "",
            "## Thinking",
            "For complex reasoning tasks, use `thinking` parameter with `budget_tokens`.",
            "Available on Opus 4.6 and Sonnet 4.6 models.",
        ]
    )

    return "\n".join(content_parts)
