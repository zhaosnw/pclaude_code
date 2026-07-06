"""
Bootstrap hooks registry.

Port of: src/types/hooks.ts, src/utils/hooks/

This minimal module exists to satisfy the import in session_file_access_hooks.py.
Hook functionality in the TS codebase lives in src/utils/hooks/, not in bootstrap.
The full hook system should be implemented in hare/utils/hooks/.
"""

from __future__ import annotations
