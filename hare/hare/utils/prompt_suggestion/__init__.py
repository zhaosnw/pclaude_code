"""Prompt suggestion utilities — text normalization, scoring, and display helpers."""

from hare.utils.prompt_suggestion.prompt_suggestion import (
    # Types
    PromptSuggestion,
    SuggestionCandidate,
    # Constants
    MAX_SUGGESTION_LENGTH,
    MAX_SUGGESTION_WORDS,
    MIN_SUGGESTION_WORDS,
    # Normalization / display
    build_suggestion_placeholder,
    normalize_suggestion_text,
    # Scoring
    rank_candidates,
    score_suggestion_quality,
    # Validation / deduplication
    deduplicate_suggestions,
    is_valid_suggestion,
)
