"""
Verify skill — validate skill definitions and output.

Port of: src/skills/bundled/verify.ts + verifyContent.ts (43 lines)
"""

from __future__ import annotations

from typing import Any


async def verify_skill_output(
    content: str,
    criteria: str = "",
    _context: Any = None,
) -> dict[str, Any]:
    """Verify skill execution output against criteria.

    Checks for common issues: empty output, error messages, incomplete results.
    """
    issues: list[dict[str, str]] = []
    content_stripped = content.strip() if content else ""

    # Check for empty output
    if not content_stripped:
        issues.append(
            {
                "severity": "error",
                "type": "empty_output",
                "message": "Skill produced no output",
            }
        )

    # Check for error indicators
    error_patterns = [
        "traceback",
        "error:",
        "exception:",
        "failed:",
        "cannot",
        "unable to",
    ]
    for pattern in error_patterns:
        if pattern in content_stripped.lower()[:500]:
            issues.append(
                {
                    "severity": "warning",
                    "type": "error_indicator",
                    "message": f"Output contains '{pattern}'",
                }
            )
            break

    # Check output length
    if len(content_stripped) < 10 and content_stripped:
        issues.append(
            {
                "severity": "warning",
                "type": "short_output",
                "message": "Output is very short (< 10 characters)",
            }
        )

    # Check criteria match
    if criteria and criteria.strip():
        criteria_lower = criteria.lower()
        if criteria_lower not in content_stripped.lower():
            issues.append(
                {
                    "severity": "warning",
                    "type": "criteria_mismatch",
                    "message": f"Criteria '{criteria}' not found in output",
                }
            )

    return {
        "verified": len([i for i in issues if i["severity"] == "error"]) == 0,
        "issues": issues,
        "output_length": len(content_stripped),
        "has_output": bool(content_stripped),
    }
