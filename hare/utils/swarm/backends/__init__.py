"""
Swarm backend registry and executors.

Port of: src/utils/swarm/backends/
"""

from hare.utils.swarm.backends.registry import (
    detect_and_get_backend,
    ensure_backends_registered,
    get_backend_by_type,
    get_cached_backend,
    get_cached_detection_result,
    get_in_process_backend,
    get_resolved_teammate_mode,
    get_teammate_executor,
    is_in_process_enabled,
    mark_in_process_fallback,
    register_iterm_backend,
    register_tmux_backend,
    reset_backend_detection,
)
from hare.utils.swarm.backends.types import (
    BackendDetectionResult,
    PaneBackendType,
)
