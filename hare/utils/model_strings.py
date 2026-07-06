"""Model string normalization helpers — TS parity re-exports."""

from __future__ import annotations


def normalize_model_string_for_api(model: str) -> str:
    """Normalize a model string for API usage (TS parity)."""
    model = model.strip()
    if model.endswith("[1m]"):
        model = model[:-4]
    return model


__all__ = ["normalize_model_string_for_api"]
