"""Port of: src/utils/modelCost.ts"""

from __future__ import annotations

MODEL_COSTS: dict[str, dict[str, float]] = {
    "claude-opus-4-0-20250514": {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-20250414": {"input": 0.25, "output": 1.25},
}


def get_model_cost(model: str) -> dict[str, float]:
    return MODEL_COSTS.get(model, {"input": 3.0, "output": 15.0})


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    costs = get_model_cost(model)
    return (input_tokens * costs["input"] + output_tokens * costs["output"]) / 1_000_000


def format_model_pricing(model: str) -> str:
    costs = get_model_cost(model)
    return f"${costs['input']}/M input, ${costs['output']}/M output"
