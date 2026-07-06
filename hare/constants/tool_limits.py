"""
Tool limits and thresholds.

Port of: src/constants/toolLimits.ts + various tool limit constants
"""

# File read/write limits
MAX_FILE_READ_LINES = 2000
MAX_FILE_READ_SIZE = 500_000
MAX_FILE_WRITE_SIZE = 500_000
MAX_GREP_RESULTS = 500
MAX_GLOB_RESULTS = 500
MAX_BASH_OUTPUT = 30_000
MAX_SEARCH_RESULTS = 100
MAX_TOOL_RESULT_SIZE = 100_000

# ---- From toolLimits.ts ----
DEFAULT_MAX_RESULT_SIZE_CHARS = (
    50_000  # Per-tool result char cap before disk persistence
)
MAX_TOOL_RESULT_TOKENS = 100_000  # ~400KB text
BYTES_PER_TOKEN = 4
MAX_TOOL_RESULT_BYTES = MAX_TOOL_RESULT_TOKENS * BYTES_PER_TOKEN
MAX_TOOL_RESULTS_PER_MESSAGE_CHARS = 200_000  # Aggregate per-message budget
TOOL_SUMMARY_MAX_LENGTH = 50
