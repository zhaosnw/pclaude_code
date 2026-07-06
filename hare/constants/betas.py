"""
Beta feature flags — mirrors every constant from the frontend betas constants.

Port of: frontend/src/constants/betas.ts
"""

CLAUDE_CODE_20250219_BETA_HEADER = "claude-code-20250219"
INTERLEAVED_THINKING_BETA_HEADER = "interleaved-thinking-2025-05-14"
CONTEXT_1M_BETA_HEADER = "context-1m-2025-08-07"
CONTEXT_MANAGEMENT_BETA_HEADER = "context-management-2025-06-27"
STRUCTURED_OUTPUTS_BETA_HEADER = "structured-outputs-2025-12-15"
WEB_SEARCH_BETA_HEADER = "web-search-2025-03-05"
# Tool search beta headers differ by provider:
# - Claude API / Foundry: advanced-tool-use-2025-11-20
# - Vertex AI / Bedrock: tool-search-tool-2025-10-19
TOOL_SEARCH_BETA_HEADER_1P = "advanced-tool-use-2025-11-20"
TOOL_SEARCH_BETA_HEADER_3P = "tool-search-tool-2025-10-19"
EFFORT_BETA_HEADER = "effort-2025-11-24"
TASK_BUDGETS_BETA_HEADER = "task-budgets-2026-03-13"
PROMPT_CACHING_SCOPE_BETA_HEADER = "prompt-caching-scope-2026-01-05"
FAST_MODE_BETA_HEADER = "fast-mode-2026-02-01"
REDACT_THINKING_BETA_HEADER = "redact-thinking-2026-02-12"
TOKEN_EFFICIENT_TOOLS_BETA_HEADER = "token-efficient-tools-2026-03-28"
SUMMARIZE_CONNECTOR_TEXT_BETA_HEADER = "summarize-connector-text-2026-03-13"
AFK_MODE_BETA_HEADER = "afk-mode-2026-01-31"
CLI_INTERNAL_BETA_HEADER = "cli-internal-2026-02-09"
ADVISOR_BETA_HEADER = "advisor-tool-2026-03-01"

# Legacy / compatibility aliases
PROMPT_CACHING_BETA = "prompt-caching-2024-07-31"
TOKEN_EFFICIENT_TOOLS_BETA = "token-efficient-tool-use-2025-02-19"

# Betas that can only go through Bedrock extraBodyParams (not headers)
BEDROCK_EXTRA_PARAMS_HEADERS = frozenset(
    {
        INTERLEAVED_THINKING_BETA_HEADER,
        CONTEXT_1M_BETA_HEADER,
        TOOL_SEARCH_BETA_HEADER_3P,
    }
)

# Betas allowed on Vertex countTokens API (other betas cause 400 errors)
VERTEX_COUNT_TOKENS_ALLOWED_BETAS = frozenset(
    {
        CLAUDE_CODE_20250219_BETA_HEADER,
        INTERLEAVED_THINKING_BETA_HEADER,
        CONTEXT_MANAGEMENT_BETA_HEADER,
    }
)
