"""
API limit constants — server-side limits enforced by the Anthropic API.

Port of: src/constants/apiLimits.ts (94 lines)
"""

# Context & output limits
MAX_CONTEXT_WINDOW_TOKENS = 200_000
MAX_OUTPUT_TOKENS = 16_384
MAX_OUTPUT_TOKENS_THINKING = 32_768
MAX_THINKING_BUDGET = 10_240
MAX_INPUT_IMAGES = 20
MAX_TOOL_RESULTS_PER_MESSAGE = 100

# Default models
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_THINKING_MODEL = "claude-opus-4-6"

# ---- Image limits (port of src/constants/apiLimits.ts) ----
API_IMAGE_MAX_BASE64_SIZE = 5 * 1024 * 1024  # 5 MB base64
IMAGE_TARGET_RAW_SIZE = int((API_IMAGE_MAX_BASE64_SIZE * 3) / 4)  # 3.75 MB raw
IMAGE_MAX_WIDTH = 2000
IMAGE_MAX_HEIGHT = 2000

# ---- PDF limits ----
PDF_TARGET_RAW_SIZE = (
    20 * 1024 * 1024
)  # 20 MB raw (fits within 32MB request limit after encoding)
API_PDF_MAX_PAGES = 100
PDF_EXTRACT_SIZE_THRESHOLD = (
    3 * 1024 * 1024
)  # 3 MB — above this, extract to page images
PDF_MAX_EXTRACT_SIZE = 100 * 1024 * 1024  # 100 MB — reject larger
PDF_MAX_PAGES_PER_READ = 20
PDF_AT_MENTION_INLINE_THRESHOLD = 10

# ---- Media limits ----
API_MAX_MEDIA_PER_REQUEST = 100
