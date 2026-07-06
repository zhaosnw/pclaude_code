"""Memory extraction service.

Port of: src/services/extractMemories/

Public API:
- extract_memories:       standalone keyword-based extraction (no forked agent)
- execute_extract_memories:  fire-and-forget forked-agent extraction (production)
- init_extract_memories:  initialize closure-scoped state (call at startup)
- drain_pending_extraction:  await in-flight extractions with soft timeout
- is_model_visible_message:  message type filter
- count_model_visible_messages_since:  count visible messages after cursor
- get_written_file_path:  extract file_path from tool_use block
- extract_written_paths:  collect all written paths from agent output
"""

from hare.services.extract_memories.extract_memories import (
    count_model_visible_messages_since,
    drain_pending_extraction,
    execute_extract_memories,
    extract_memories,
    extract_written_paths,
    get_written_file_path,
    init_extract_memories,
    is_model_visible_message,
)
from hare.services.extract_memories.prompts import (
    MEMORY_EXTRACTION_PROMPT,
    build_extract_auto_only_prompt,
    build_extract_combined_prompt,
)

__all__ = [
    "MEMORY_EXTRACTION_PROMPT",
    "build_extract_auto_only_prompt",
    "build_extract_combined_prompt",
    "count_model_visible_messages_since",
    "drain_pending_extraction",
    "execute_extract_memories",
    "extract_memories",
    "extract_written_paths",
    "get_written_file_path",
    "init_extract_memories",
    "is_model_visible_message",
]
