"""
Register skill-related hooks for improvement and sampling.

Port of: src/utils/hooks/registerSkillHooks.ts

Hooks into the skill execution lifecycle to collect usage data,
improvement suggestions, and feedback for skills. Also provides
hook discovery, validation, and lifecycle management.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------

class SkillHookPhase(Enum):
    INVOKED = "invoked"
    COMPLETED = "completed"
    FAILED = "failed"
    IMPROVEMENT = "improvement"

def _phase_from_string(value: str) -> Optional[SkillHookPhase]:
    try:
        return SkillHookPhase(value.lower())
    except ValueError:
        return None

# Minimum expected parameters per phase: (name, context/result/feedback, ...)
_MIN_PARAMS: dict[SkillHookPhase, int] = {
    SkillHookPhase.INVOKED: 2,
    SkillHookPhase.COMPLETED: 3,
    SkillHookPhase.FAILED: 3,
    SkillHookPhase.IMPROVEMENT: 2,
}

@dataclass
class SkillHook:
    """A registered skill hook with metadata and health tracking."""
    name: str
    phase: SkillHookPhase
    callback: Callable[..., Any]
    source: str = "code"
    priority: int = 50
    enabled: bool = True
    max_failures: int = 5
    timeout_seconds: float = 30.0
    dependencies: list[str] = field(default_factory=list)
    error_count: int = field(default=0, repr=False)
    last_error: str = field(default="", repr=False)
    last_run_at: float = field(default=0.0, repr=False)
    registered_at: float = field(default_factory=time.time, repr=False)

# ---------------------------------------------------------------------------
# Hook registry
# ---------------------------------------------------------------------------

class SkillHookRegistry:
    """Central registry for skill hooks with discovery, validation,
    priority ordering, enable/disable, auto-disable on failures,
    and startup/shutdown lifecycle."""

    def __init__(self) -> None:
        self._hooks: dict[SkillHookPhase, list[SkillHook]] = {
            p: [] for p in SkillHookPhase
        }
        self._names: set[str] = set()
        self._shutdown: bool = False

    # -- Registration --------------------------------------------

    def register(self, hook: SkillHook) -> bool:
        error = validate_skill_hook(hook.name, hook.phase.value, hook.callback)
        if error:
            from hare.utils.debug import log_for_debugging as _d
            _d(f"SkillHookRegistry: rejected '{hook.name}': {error}")
            return False
        key = f"{hook.phase.value}:{hook.name}"
        if key in self._names:
            return False
        self._names.add(key)
        self._hooks[hook.phase].append(hook)
        self._hooks[hook.phase].sort(key=lambda h: (h.priority, h.registered_at))
        return True

    def unregister(self, name: str, phase: SkillHookPhase) -> bool:
        self._names.discard(f"{phase.value}:{name}")
        before = len(self._hooks[phase])
        self._hooks[phase] = [h for h in self._hooks[phase] if h.name != name]
        return len(self._hooks[phase]) < before

    def get_hooks(self, phase: SkillHookPhase) -> list[SkillHook]:
        if self._shutdown:
            return []
        return [h for h in self._hooks[phase] if h.enabled]

    def list_all(self) -> list[SkillHook]:
        result: list[SkillHook] = []
        for p in SkillHookPhase:
            result.extend(self._hooks[p])
        return result

    def clear(self) -> None:
        for p in SkillHookPhase:
            self._hooks[p].clear()
        self._names.clear()

    # -- Discovery -----------------------------------------------

    def discover_from_settings(self, settings_path: Optional[str] = None) -> int:
        if settings_path:
            return self._load_file(Path(settings_path))
        count = 0
        for p in [Path.home() / ".hare" / "settings.json",
                  Path.cwd() / ".hare" / "settings.json",
                  Path.cwd() / ".hare" / "settings.local.json"]:
            if p.is_file():
                count += self._load_file(p)
        return count

    def discover_from_plugins(self) -> int:
        root = Path.home() / ".hare" / "plugins"
        if not root.is_dir():
            return 0
        count = 0
        for hf in root.glob("*/hooks/skill_hooks.json"):
            count += self._load_file(hf)
        return count

    def _load_file(self, path: Path) -> int:
        discovered = 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0
        entries = data.get("skillHooks") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            return 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            cb = self._resolve(entry.get("path", ""))
            if cb is None:
                continue
            phase = _phase_from_string(entry.get("phase", ""))
            if phase is None:
                continue
            if self.register(SkillHook(
                name=entry.get("name", f"discovered_{path.stem}_{discovered}"),
                phase=phase, callback=cb, source="settings",
                priority=int(entry.get("priority", 50)),
                enabled=bool(entry.get("enabled", True)),
                dependencies=entry.get("dependencies", []),
            )):
                discovered += 1
        return discovered

    @staticmethod
    def _resolve(dotted_path: str) -> Optional[Callable[..., Any]]:
        if not dotted_path or ":" not in dotted_path:
            return None
        mod_path, _, func = dotted_path.rpartition(":")
        try:
            return getattr(importlib.import_module(mod_path), func, None)
        except (ImportError, AttributeError):
            return None

    # -- Lifecycle -----------------------------------------------

    def record_success(self, name: str, phase: SkillHookPhase) -> None:
        h = self._find(name, phase)
        if h:
            h.error_count = 0
            h.last_error = ""
            h.last_run_at = time.time()

    def record_failure(self, name: str, phase: SkillHookPhase, error: str) -> None:
        h = self._find(name, phase)
        if not h:
            return
        h.error_count += 1
        h.last_error = error[:500]
        h.last_run_at = time.time()
        if h.error_count >= h.max_failures:
            h.enabled = False
            from hare.utils.debug import log_for_debugging as _d
            _d(f"SkillHookRegistry: auto-disabled '{name}' after {h.error_count} failures")

    def enable_hook(self, name: str, phase: SkillHookPhase) -> bool:
        h = self._find(name, phase)
        if h:
            h.enabled, h.error_count, h.last_error = True, 0, ""
            return True
        return False

    def disable_hook(self, name: str, phase: SkillHookPhase) -> bool:
        h = self._find(name, phase)
        if h:
            h.enabled = False
            return True
        return False

    def health_report(self) -> dict[str, Any]:
        total = enabled = 0
        failed: list[str] = []
        for p in SkillHookPhase:
            for h in self._hooks[p]:
                total += 1
                if h.enabled:
                    enabled += 1
                elif h.last_error:
                    failed.append(f"{p.value}:{h.name}")
        return {"total": total, "enabled": enabled, "disabled": total - enabled,
                "disabled_with_errors": failed, "shutdown": self._shutdown}

    async def startup(self) -> dict[str, Any]:
        sc = self.discover_from_settings()
        pc = self.discover_from_plugins()
        return {**self.health_report(), "discovered_settings": sc, "discovered_plugins": pc}

    async def shutdown(self) -> None:
        self._shutdown = True
        await asyncio.sleep(0)

    def _find(self, name: str, phase: SkillHookPhase) -> Optional[SkillHook]:
        for h in self._hooks[phase]:
            if h.name == name:
                return h
        return None

# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_registry: Optional[SkillHookRegistry] = None

def get_skill_hook_registry() -> SkillHookRegistry:
    global _registry
    if _registry is None:
        _registry = SkillHookRegistry()
    return _registry

# ---------------------------------------------------------------------------
# Legacy callback lists
# ---------------------------------------------------------------------------

_skill_invoked_callbacks: list[Callable[[str, dict[str, Any]], Any]] = []
_skill_completed_callbacks: list[Callable[[str, str, dict[str, Any]], Any]] = []
_skill_failed_callbacks: list[Callable[[str, str, dict[str, Any]], Any]] = []
_skill_improvement_callbacks: list[Callable[[str, str], Any]] = []

_LEGACY_MAP: dict[SkillHookPhase, list[Callable[..., Any]]] = {
    SkillHookPhase.INVOKED: _skill_invoked_callbacks,
    SkillHookPhase.COMPLETED: _skill_completed_callbacks,
    SkillHookPhase.FAILED: _skill_failed_callbacks,
    SkillHookPhase.IMPROVEMENT: _skill_improvement_callbacks,
}

def _migrate_legacy_callbacks() -> None:
    registry = get_skill_hook_registry()
    for phase, cbs in _LEGACY_MAP.items():
        for idx, cb in enumerate(cbs):
            registry.register(SkillHook(
                name=f"legacy:{phase.value}:{idx}", phase=phase,
                callback=cb, source="legacy"))
        cbs.clear()

# ---------------------------------------------------------------------------
# Public registration API
# ---------------------------------------------------------------------------

def on_skill_invoked(callback: Callable[[str, dict[str, Any]], Any]) -> None:
    _skill_invoked_callbacks.append(callback)

def on_skill_completed(callback: Callable[[str, str, dict[str, Any]], Any]) -> None:
    _skill_completed_callbacks.append(callback)

def on_skill_failed(callback: Callable[[str, str, dict[str, Any]], Any]) -> None:
    _skill_failed_callbacks.append(callback)

def on_skill_improvement(callback: Callable[[str, str], Any]) -> None:
    _skill_improvement_callbacks.append(callback)

# ---------------------------------------------------------------------------
# Async dispatch
# ---------------------------------------------------------------------------

async def _run_hook(hook: SkillHook, *args: Any) -> Optional[Any]:
    registry = get_skill_hook_registry()
    try:
        result = hook.callback(*args)
        if asyncio.iscoroutine(result):
            result = await asyncio.wait_for(result, timeout=hook.timeout_seconds)
        registry.record_success(hook.name, hook.phase)
        return result
    except asyncio.TimeoutError:
        registry.record_failure(hook.name, hook.phase, f"Timeout after {hook.timeout_seconds}s")
    except Exception as exc:
        registry.record_failure(hook.name, hook.phase, repr(exc))
    return None

async def _dispatch(phase: SkillHookPhase, *args: Any) -> list[Any]:
    _migrate_legacy_callbacks()
    hooks = get_skill_hook_registry().get_hooks(phase)
    if not hooks:
        return []
    return await asyncio.gather(
        *[_run_hook(h, *args) for h in hooks], return_exceptions=True)

async def notify_skill_invoked(skill_name: str, context: dict[str, Any]) -> None:
    await _dispatch(SkillHookPhase.INVOKED, skill_name, context)

async def notify_skill_completed(skill_name: str, result: str, metadata: dict[str, Any]) -> None:
    await _dispatch(SkillHookPhase.COMPLETED, skill_name, result, metadata)

async def notify_skill_failed(skill_name: str, error: str, metadata: dict[str, Any]) -> None:
    await _dispatch(SkillHookPhase.FAILED, skill_name, error, metadata)

async def collect_skill_improvements(skill_name: str, feedback: str) -> list[str]:
    results = await _dispatch(SkillHookPhase.IMPROVEMENT, skill_name, feedback)
    return [r for r in results if isinstance(r, str) and r.strip()]

# ---------------------------------------------------------------------------
# Public validation + discovery API
# ---------------------------------------------------------------------------

def validate_skill_hook(name: str, phase: str, callback: Callable[..., Any]) -> Optional[str]:
    """Validate a skill hook. Returns None if valid, else an error message."""
    ph = _phase_from_string(phase.lower())
    if ph is None:
        return f"Unknown phase '{phase}'. Valid: invoked, completed, failed, improvement"
    if not name or not name.strip():
        return "Hook name must not be empty"
    try:
        sig = inspect.signature(callback)
    except (ValueError, TypeError) as e:
        return f"Cannot introspect callback: {e}"
    real = [p for p in sig.parameters.values()
            if p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)]
    needed = _MIN_PARAMS.get(ph, 0)
    if len(real) < needed:
        return f"Callback needs >= {needed} params for phase '{phase}' (got {len(real)})"
    return None

def discover_available_hooks() -> list[dict[str, Any]]:
    """Scan all sources for skillHooks entries without registering them."""
    sources: list[Path] = [
        Path.home() / ".hare" / "settings.json",
        Path.cwd() / ".hare" / "settings.json",
        Path.cwd() / ".hare" / "settings.local.json",
    ]
    found: list[dict[str, Any]] = []
    for sp in sources:
        if not sp.is_file():
            continue
        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        entries = data.get("skillHooks") if isinstance(data, dict) else None
        if isinstance(entries, list):
            for e in entries:
                if isinstance(e, dict):
                    found.append({"source_file": str(sp), **e})
    root = Path.home() / ".hare" / "plugins"
    if root.is_dir():
        for hf in root.glob("*/hooks/skill_hooks.json"):
            try:
                data = json.loads(hf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            entries = data.get("skillHooks") if isinstance(data, dict) else None
            if isinstance(entries, list):
                for e in entries:
                    if isinstance(e, dict):
                        found.append({"source_file": str(hf), **e})
    return found

# ---------------------------------------------------------------------------
# Top-level wiring
# ---------------------------------------------------------------------------

def register_skill_hooks(app: Any) -> None:
    """Wire skill lifecycle hooks into the application."""
    on_skill_invoked(_log_skill_invocation)
    on_skill_completed(_log_skill_completion)
    on_skill_failed(_log_skill_failure)

    try:
        from hare.services.analytics.growthbook import is_feature_enabled
        if is_feature_enabled("skill_improvement_hooks"):
            on_skill_improvement(_collect_improvement_suggestion)
    except ImportError:
        pass

    _migrate_legacy_callbacks()
    try:
        asyncio.get_running_loop().create_task(get_skill_hook_registry().startup())
    except RuntimeError:
        pass

# ---------------------------------------------------------------------------
# Private analytics callbacks
# ---------------------------------------------------------------------------

def _log_skill_invocation(skill_name: str, context: dict[str, Any]) -> None:
    try:
        from hare.services.analytics import log_event
        log_event("skill_invoked", {"skill_name": skill_name, "source": context.get("source", "unknown")})
    except ImportError:
        pass

def _log_skill_completion(skill_name: str, result: str, metadata: dict[str, Any]) -> None:
    try:
        from hare.services.analytics import log_event
        log_event("skill_completed", {"skill_name": skill_name, "duration_ms": metadata.get("duration_ms", 0)})
    except ImportError:
        pass

def _log_skill_failure(skill_name: str, error: str, metadata: dict[str, Any]) -> None:
    try:
        from hare.services.analytics import log_event
        log_event("skill_failed", {"skill_name": skill_name, "error": error[:200]})
    except ImportError:
        pass

def _collect_improvement_suggestion(skill_name: str, feedback: str) -> Optional[str]:
    return f"[{skill_name}] Consider improvement: {feedback[:100]}"
