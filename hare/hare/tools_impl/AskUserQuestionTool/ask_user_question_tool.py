"""
AskUserQuestionTool – ask user multiple-choice questions.

Port of: src/tools/AskUserQuestionTool/

Presents structured questions to the user and collects their answers.
Supports single-select, multi-select, and free-text responses.
"""

from __future__ import annotations
from typing import Any

TOOL_NAME = "AskUserQuestion"


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "The question to ask"},
                        "header": {"type": "string", "description": "Short label (max 12 chars)"},
                        "options": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 4,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string", "description": "Display text (1-5 words)"},
                                    "description": {"type": "string", "description": "Explanation of what this option means"},
                                },
                                "required": ["label", "description"],
                            },
                        },
                        "multiSelect": {"type": "boolean", "default": False},
                    },
                    "required": ["question", "header", "options", "multiSelect"],
                },
            },
        },
        "required": ["questions"],
    }


def validate_input(input: dict[str, Any]) -> dict[str, Any]:
    """Validate question format before presenting to user."""
    questions = input.get("questions", [])
    errors = []

    if not questions:
        return {"result": False, "message": "At least one question is required."}

    for i, q in enumerate(questions):
        q_num = f"Question {i+1}"
        header = q.get("header", "")
        if len(str(header)) > 12:
            errors.append(f"{q_num}: header exceeds 12 character limit")
        options = q.get("options", [])
        if len(options) < 2:
            errors.append(f"{q_num}: needs at least 2 options")
        if len(options) > 4:
            errors.append(f"{q_num}: max 4 options allowed")
        for j, opt in enumerate(options):
            label = opt.get("label", "")
            if not label.strip():
                errors.append(f"{q_num}, Option {j+1}: label is required")

    if errors:
        return {"result": False, "message": "; ".join(errors)}
    return {"result": True}


async def call(questions: list[dict[str, Any]] | None = None, **kwargs: Any) -> dict[str, Any]:
    """Present questions and collect answers.

    In interactive mode, this triggers the permission system to show
    a UI prompt. In non-interactive/SDK mode, answers must come from
    the structured I/O layer.
    """
    if not questions:
        return {"error": "No questions provided."}

    # Validate questions
    validation = validate_input({"questions": questions})
    if not validation["result"]:
        return {"error": validation["message"]}

    # Try to get answers from context (pre-filled in SDK/test mode)
    prefill = kwargs.get("_answers") or kwargs.get("answers")
    if isinstance(prefill, dict):
        return {"questions": questions, "answers": prefill, "status": "completed"}

    # In real interactive mode, questions are presented to user
    # via the permission/can-use-tool pipeline
    answers: list[dict[str, Any]] = []
    for q in questions:
        answers.append({
            "question": q.get("question", ""),
            "header": q.get("header", ""),
            "answer": "(awaiting user response — select from options)",
            "options": q.get("options", []),
            "multiSelect": q.get("multiSelect", False),
        })

    return {
        "questions": answers,
        "status": "pending",
        "message": f"{len(questions)} question(s) presented to user.",
    }


def user_facing_name(input: dict[str, Any] | None = None) -> str:
    return "Ask User Question"


def is_concurrency_safe(input: dict[str, Any]) -> bool:
    return False
