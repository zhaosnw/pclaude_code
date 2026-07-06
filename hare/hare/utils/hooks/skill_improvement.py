"""
Skill improvement loop (post-sampling).

Port of: src/utils/hooks/skillImprovement.ts

Collects feedback, tracks performance, generates improvement suggestions
via heuristic analysis. Integrates into the post-sampling hook pipeline.
"""

from __future__ import annotations

import datetime, json, os, re
from dataclasses import dataclass
from typing import Any

from hare.utils.debug import log_for_debugging

_MAX = 5
_MAX_FB = 50
_STOP = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "it", "and", "or", "but",
    "this", "that", "to", "of", "in", "for", "on", "with", "as", "at", "by",
    "from", "be", "has", "have", "had", "not", "should", "would", "could",
    "can", "will", "just", "very", "too", "i", "me", "my", "you", "your",
})
_P = {"error_handling": 5, "dependencies": 5, "description": 4, "performance": 3, "maturity": 1}


@dataclass
class SkillUpdate:
    section: str
    change: str
    reason: str


@dataclass
class SkillFeedback:
    skill_name: str
    rating: int
    comment: str = ""
    source: str = ""
    timestamp: str = ""
    duration_ms: int = 0
    error: str = ""


@dataclass
class SkillPerformance:
    skill_name: str
    invocations: int = 0
    completions: int = 0
    failures: int = 0
    total_duration_ms: int = 0
    avg_rating: float = 0.0
    ratings_count: int = 0
    last_used_at: str = ""

    @property
    def success_rate(self) -> float:
        t = self.completions + self.failures
        return self.completions / t if t else 1.0

    @property
    def avg_dur_ms(self) -> float:
        return self.total_duration_ms / self.completions if self.completions else 0.0


_fb: list[SkillFeedback] = []
_perf: dict[str, SkillPerformance] = {}


def collect_feedback(
    skill_name: str, rating: int, comment: str = "", source: str = "auto",
    duration_ms: int = 0, error: str = "",
) -> SkillFeedback:
    """Record feedback, update performance metrics."""
    r = max(1, min(5, rating)); n = skill_name.strip()
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    e = SkillFeedback(n, r, comment[:500], source, ts, duration_ms, error[:200])
    if n not in _perf: _perf[n] = SkillPerformance(n)
    p = _perf[n]; p.invocations += 1
    if error: p.failures += 1
    else: p.completions += 1
    p.total_duration_ms += duration_ms
    if r > 0:
        p.avg_rating = (p.avg_rating * p.ratings_count + r) / (p.ratings_count + 1)
        p.ratings_count += 1
    p.last_used_at = ts
    _fb.append(e)
    if len(_fb) > _MAX_FB: _fb.pop(0)
    _persist(e); return e


def _persist(entry: SkillFeedback) -> None:
    d = os.path.join(os.getcwd(), ".claude/skill_improvements")
    if not os.path.isdir(d): return
    try:
        with open(os.path.join(d, "improvements.jsonl"), "a") as fh:
            fh.write(json.dumps(entry.__dict__) + "\n")
    except OSError as exc:
        log_for_debugging(f"skill_improvement: persist: {exc}")


def get_feedback(skill_name: str) -> list[SkillFeedback]:
    n = skill_name.strip(); return [x for x in _fb if x.skill_name == n]


def get_performance(skill_name: str) -> SkillPerformance:
    return _perf.get(skill_name.strip(), SkillPerformance(skill_name))


def clear_feedback(skill_name: str | None = None) -> None:
    global _fb
    if skill_name is None: _fb.clear(); _perf.clear()
    else:
        n = skill_name.strip()
        _fb[:] = [x for x in _fb if x.skill_name != n]; _perf.pop(n, None)


def generate_improvements(skill_name: str) -> list[SkillUpdate]:
    """Return prioritized improvement suggestions from performance analysis.

    Heuristics: success_rate<60%→error handling, rating<3.0→clarify docs,
    ≥3 quick failures→check deps, ≥10 uses+≥90% success+≥4★→promote stable,
    avg_dur>30s→profile performance.
    """
    p = get_performance(skill_name); fbs = get_feedback(skill_name)
    sr, ar, dur = p.success_rate, p.avg_rating, p.avg_dur_ms
    u: list[SkillUpdate] = []

    if p.invocations >= 3 and sr < 0.6:
        u.append(SkillUpdate("error_handling",
            f"Add fallback + validation for '{skill_name}' (rate {sr:.0%})",
            f"Rate {sr:.0%}<60% over {p.invocations} calls."))
    if ar > 0 and ar < 3.0 and p.ratings_count >= 2:
        from collections import Counter
        kw: list[str] = []
        for fb in fbs:
            for w in fb.comment.lower().split():
                w = w.strip(".,!?;:()[]{}\"'")
                if len(w) >= 3 and w not in _STOP:
                    kw.append(w)
        themes = [t[0] for t in Counter(kw).most_common(5)]
        u.append(SkillUpdate("description",
            f"Clarify '{skill_name}' instructions"+(
                f" — themes: {', '.join(themes)}" if themes else ""),
            f"Avg rating {ar:.1f}/5 over {p.ratings_count} ratings."))
    if p.failures >= 3 and dur < 500:
        u.append(SkillUpdate("dependencies",
            f"Check deps/imports for '{skill_name}' ({p.failures} exits, {dur:.0f}ms)",
            f"{p.failures} short-lived failures — missing deps?"))
    if p.invocations >= 10 and sr >= 0.9 and ar >= 4.0:
        u.append(SkillUpdate("maturity",
            f"'{skill_name}' is stable — promote to stable.",
            f"{p.invocations} calls, {sr:.0%} success, {ar:.1f}/5."))
    if dur > 30000 and p.completions >= 3:
        u.append(SkillUpdate("performance",
            f"Profile '{skill_name}' (avg {dur/1000:.1f}s).",
            f"Avg >30s over {p.completions} completions."))
    return u[:_MAX]


async def maybe_run_skill_improvement(
    _messages: list[Any], _context: dict[str, Any]
) -> list[SkillUpdate]:
    """Inspect messages for skill invocations and return improvement suggestions."""
    skills: set[str] = set()
    for msg in _messages:
        c = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
        if isinstance(c, list):
            for blk in c:
                if (isinstance(blk, dict) and blk.get("type") == "tool_use"
                        and blk.get("name") == "Skill"
                        and isinstance(blk.get("input"), dict)):
                    sk = blk["input"].get("skill", "")
                    if sk: skills.add(sk.strip())
        elif isinstance(c, str):
            for m in re.finditer(r"/([a-z][a-z0-9_-]{1,60})", c):
                skills.add(m.group(1))
    known = {n for n, p in _perf.items() if p.invocations > 0}
    skills.update(known & skills)
    if not skills: return []
    all_u: list[SkillUpdate] = []
    for n in skills: all_u.extend(generate_improvements(n))
    all_u.sort(key=lambda u: _P.get(u.section, 2), reverse=True)
    if all_u:
        log_for_debugging(f"skill_improvement: {len(all_u)} suggestions / {len(skills)} skills")
    return all_u[:_MAX]


async def skill_improvement_post_sampling_hook(ctx: dict[str, Any]) -> list[SkillUpdate]:
    """Post-sampling hook entry point."""
    return await maybe_run_skill_improvement(
        ctx.get("messages", ctx.get("transcript", [])), ctx)


def register_skill_improvement_hooks() -> None:
    """Wire into the post-sampling hook pipeline."""
    try:
        from hare.utils.hooks.post_sampling_hooks import register_post_sampling_hook
        register_post_sampling_hook(skill_improvement_post_sampling_hook)
        log_for_debugging("skill_improvement: registered")
    except ImportError as e:
        log_for_debugging(f"skill_improvement: failed: {e}")
