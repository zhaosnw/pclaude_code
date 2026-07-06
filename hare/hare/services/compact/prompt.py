"""
Compact prompts with dual-stage output structure.

Port of: src/services/compact/prompt.ts

Key design (matching TS):
- NO_TOOLS_PREAMBLE: prevents tool calls during compaction
- Dual-stage output: <analysis> (discarded) + <summary> (kept, 9 chapters)
- Three templates: BASE, PARTIAL (from direction), PARTIAL_UP_TO (up_to direction)
- formatCompactSummary: strips analysis, extracts summary
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Preamble / trailer (TS prompt.ts)
# ---------------------------------------------------------------------------

NO_TOOLS_PREAMBLE = """\
CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.
"""

NO_TOOLS_TRAILER = ""
# In TS, the trailer is empty; the preamble already contains the critical instruction.

# ---------------------------------------------------------------------------
# Analysis instructions (TS DETAILED_ANALYSIS_INSTRUCTION_BASE / _PARTIAL)
# ---------------------------------------------------------------------------

DETAILED_ANALYSIS_INSTRUCTION_BASE = (
    "Before providing your final summary, wrap your analysis in <analysis> tags "
    "to organize your thoughts and ensure you've covered all necessary points. "
    "In your analysis process:\n\n"
    "1. Chronologically analyze each message and section of the conversation. "
    "For each section thoroughly identify:\n"
    "   - The user's explicit requests and intents\n"
    "   - Your approach to addressing the user's requests\n"
    "   - Key decisions, technical concepts and code patterns\n"
    "   - Specific details like:\n"
    "     - file names\n"
    "     - full code snippets\n"
    "     - function signatures\n"
    "     - file edits\n"
    "2. Double-check for technical accuracy and completeness, addressing each "
    "required element thoroughly."
)

DETAILED_ANALYSIS_INSTRUCTION_PARTIAL = (
    "Before providing your final summary, wrap your analysis in <analysis> tags "
    "to organize your thoughts and ensure you've covered all necessary points. "
    "In your analysis process:\n\n"
    "1. Analyze the recent messages chronologically. For each section thoroughly "
    "identify:\n"
    "   - The user's explicit requests and intents\n"
    "   - Your approach to addressing the user's requests\n"
    "   - Key decisions, technical concepts and code patterns\n"
    "   - Specific details like file names, full code snippets, function "
    "signatures, file edits\n"
    "2. Double-check for technical accuracy and completeness, addressing each "
    "required element thoroughly."
)

# ---------------------------------------------------------------------------
# 9-section summary structure (shared across templates)
# ---------------------------------------------------------------------------

_SUMMARY_SECTIONS_BASE = """\
1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Pay special attention to the most recent messages and include full code snippets where applicable and include a summary of why this file read or edit is important.
4. Errors and fixes: List all errors that you ran into, and how you fixed them. Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results. These are critical for understanding the users' feedback and changing intent.
7. Pending Tasks: Outline any pending tasks that you have explicitly been asked to work on.
8. Current Work: Describe in detail precisely what was being worked on immediately before this summary request, paying special attention to the most recent messages from both user and assistant. Include file names and code snippets where applicable.
9. Optional Next Step: List the next step that you will take that is related to the most recent work you were doing. IMPORTANT: ensure that this step is DIRECTLY in line with the user's most recent explicit requests, and the task you were working on immediately before this summary request. If your last task was concluded, then only list next steps if they are explicitly in line with the users request. Do not start on tangential requests or really old requests that were already completed without confirming with the user first.
                       If there is a next step, include direct quotes from the most recent conversation showing exactly what task you were working on and where you left off. This should be verbatim to ensure there's no drift in task interpretation."""

_SUMMARY_SECTIONS_PARTIAL = """\
1. Primary Request and Intent: Capture the user's explicit requests and intents from the recent messages
2. Key Technical Concepts: List important technical concepts, technologies, and frameworks discussed recently.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Include full code snippets where applicable and include a summary of why this file read or edit is important.
4. Errors and fixes: List errors encountered and how they were fixed.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages from the recent portion that are not tool results.
7. Pending Tasks: Outline any pending tasks from the recent messages.
8. Current Work: Describe precisely what was being worked on immediately before this summary request.
9. Optional Next Step: List the next step related to the most recent work. Include direct quotes from the most recent conversation."""

_SUMMARY_SECTIONS_UP_TO = """\
1. Primary Request and Intent: Capture the user's explicit requests and intents in detail
2. Key Technical Concepts: List important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Include full code snippets where applicable and include a summary of why this file read or edit is important.
4. Errors and fixes: List errors encountered and how they were fixed.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results.
7. Pending Tasks: Outline any pending tasks.
8. Work Completed: Describe what was accomplished by the end of this portion.
9. Context for Continuing Work: Summarize any context, decisions, or state that would be needed to understand and continue the work in subsequent messages."""

# ---------------------------------------------------------------------------
# Three prompt templates (TS BASE / PARTIAL / PARTIAL_UP_TO)
# ---------------------------------------------------------------------------

BASE_COMPACT_PROMPT = f"""Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, and architectural decisions that would be essential for continuing development work without losing context.

{DETAILED_ANALYSIS_INSTRUCTION_BASE}

Your summary should include the following sections:

{_SUMMARY_SECTIONS_BASE}

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
1. Primary Request and Intent:
   [Detailed description]

2. Key Technical Concepts:
   - [Concept 1]
   - [Concept 2]

...
</summary>
</example>"""

PARTIAL_COMPACT_PROMPT = f"""Your task is to create a detailed summary of the RECENT portion of the conversation — the messages that follow earlier retained context. The earlier messages are being kept intact and do NOT need to be summarized. Focus your summary on what was discussed, learned, and accomplished in the recent messages only.

{DETAILED_ANALYSIS_INSTRUCTION_PARTIAL}

Your summary should include the following sections:

{_SUMMARY_SECTIONS_PARTIAL}

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
1. Primary Request and Intent:
   [Detailed description]
...
</summary>
</example>"""

PARTIAL_COMPACT_UP_TO_PROMPT = f"""Your task is to create a detailed summary of this conversation. This summary will be placed at the start of a continuing session; newer messages that build on this context will follow after your summary (you do not see them here). Summarize thoroughly so that someone reading only your summary and then the newer messages can fully understand what happened and continue the work.

{DETAILED_ANALYSIS_INSTRUCTION_BASE}

Your summary should include the following sections:

{_SUMMARY_SECTIONS_UP_TO}

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
1. Primary Request and Intent:
   [Detailed description]
...
</summary>
</example>"""

# ---------------------------------------------------------------------------
# Legacy compat
# ---------------------------------------------------------------------------

COMPACT_SYSTEM_PROMPT = BASE_COMPACT_PROMPT


# ---------------------------------------------------------------------------
# Prompt factory functions (TS buildCompactPrompt)
# ---------------------------------------------------------------------------


def get_compact_prompt(
    custom_instructions: str = "",
    *,
    direction: str = "base",
) -> str:
    """Build compact prompt matching TS buildCompactPrompt.

    Args:
        custom_instructions: Optional additional instructions appended to prompt.
        direction: 'base' (full), 'from' (partial), or 'up_to' (partial up-to).
    """
    if direction == "up_to":
        template = PARTIAL_COMPACT_UP_TO_PROMPT
    elif direction == "from":
        template = PARTIAL_COMPACT_PROMPT
    else:
        template = BASE_COMPACT_PROMPT

    prompt = NO_TOOLS_PREAMBLE + template

    if custom_instructions and custom_instructions.strip():
        prompt += f"\n\nAdditional Instructions:\n{custom_instructions}"

    return prompt


def get_partial_compact_prompt(direction: str = "from") -> str:
    """Get partial compact prompt."""
    if direction == "up_to":
        return NO_TOOLS_PREAMBLE + PARTIAL_COMPACT_UP_TO_PROMPT
    return NO_TOOLS_PREAMBLE + PARTIAL_COMPACT_PROMPT


def get_compact_user_summary_message(summary: str) -> str:
    """Format a compact summary as a user message."""
    return f"<conversation_summary>\n{summary}\n</conversation_summary>"


# ---------------------------------------------------------------------------
# Summary post-processing (TS formatCompactSummary)
# ---------------------------------------------------------------------------


def format_compact_summary(raw: str) -> str:
    """Format compact summary: strip <analysis>, extract <summary>.

    TS formatCompactSummary:
    1. Remove <analysis>...</analysis> block (drafting scratchpad)
    2. Extract <summary>...</summary> content
    3. Replace with formatted header

    The analysis block improves summary quality but has no informational
    value once the summary is written — discard it to save tokens.
    """
    formatted = raw

    # Strip analysis section
    formatted = re.sub(
        r"<analysis>[\s\S]*?</analysis>",
        "",
        formatted,
    )

    # Extract and format summary section
    m = re.search(r"<summary>([\s\S]*?)</summary>", formatted)
    if m:
        content = m.group(1) or ""
        formatted = re.sub(
            r"<summary>[\s\S]*?</summary>",
            f"Summary:\n{content.strip()}",
            formatted,
        )

    # Clean up multiple blank lines
    formatted = re.sub(r"\n{3,}", "\n\n", formatted)

    return formatted.strip()
