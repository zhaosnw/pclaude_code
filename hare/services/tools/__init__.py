"""
Tool execution services: hooks, execution, streaming.

Port of: src/services/tools/
"""

from hare.services.tools.tool_hooks import (
    run_pre_tool_use_hooks,
    run_post_tool_use_hooks,
    run_post_tool_use_failure_hooks,
    resolve_hook_permission_decision,
)
from hare.services.tools.tool_execution import (
    check_permissions_and_call_tool,
)
from hare.services.tools.streaming_tool_executor import (
    StreamingToolExecutor,
)
