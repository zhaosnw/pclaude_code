"""Golden-based CLI E2E for the nested hare project.

Each hare/alignment/cases/**/case.json is run through the real CLI subprocess
(e2e_runner.run_case), normalized, and compared to its recorded golden under
hare/alignment/golden/<same-relative-path>/golden.json.

Golden sources:
- policy.golden_source == "self": deterministic output, golden frozen from hare
  itself after human review (e.g. --version/--help). No model involved.
- otherwise: golden recorded from the TS reference via scripts/record_golden.py
  (the real differential).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARE_ROOT = REPO_ROOT / "hare"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(HARE_ROOT / "alignment"))

from e2e_runner import run_case  # noqa: E402
from golden_normalize import compare_file_effects, normalize_result  # noqa: E402

CASES_DIR = HARE_ROOT / "alignment" / "cases"
GOLDEN_DIR = HARE_ROOT / "alignment" / "golden"


def _find_result_obj(stdout: str, stdout_kind: str):
    """Pull the result-type JSON object out of stdout (json or ndjson)."""
    if stdout_kind == "ndjson":
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "result":
                return obj
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def _stable_result(obj: dict) -> dict:
    """Stable, environment-independent subset of a result object. Excludes
    volatile fields (duration_ms/total_cost_usd/session_id/uuid/model_usage) and
    schema extras that differ across versions."""
    usage = obj.get("usage") or {}
    return {
        "type": obj.get("type"),
        "subtype": obj.get("subtype"),
        "is_error": obj.get("is_error"),
        "result": obj.get("result"),
        "num_turns": obj.get("num_turns"),
        "stop_reason": obj.get("stop_reason"),
        "usage_input_tokens": usage.get("input_tokens"),
        "usage_output_tokens": usage.get("output_tokens"),
    }

CASE_PATHS = sorted(CASES_DIR.glob("**/case.json"))


@pytest.mark.integration
@pytest.mark.parametrize(
    "case_path",
    CASE_PATHS,
    ids=[json.loads(p.read_text())["case_id"] for p in CASE_PATHS],
)
def test_case_matches_golden(case_path: Path):
    case = json.loads(case_path.read_text(encoding="utf-8"))

    # Safety: hare's real model client uses ambient OAuth creds and IGNORES
    # ANTHROPIC_BASE_URL, so a model-driven case without a fixture would make a
    # real, nondeterministic, BILLED API call. Every model-driven case must
    # declare a fixture; CLI-only cases must mark kind == "deterministic".
    if case.get("kind") != "deterministic":
        assert case.get("fixture"), (
            f"{case['case_id']}: model-driven case must declare a 'fixture' "
            f"(else hare hits the real API). Or set kind='deterministic' for "
            f"CLI-only cases that never reach the model."
        )

    rel = case_path.parent.relative_to(CASES_DIR)
    golden_path = GOLDEN_DIR / rel / "golden.json"
    assert golden_path.exists(), (
        f"missing golden for {case['case_id']}: {golden_path}\n"
        f"record it with: python scripts/record_golden.py {case['case_id']}"
    )
    golden = json.loads(golden_path.read_text(encoding="utf-8"))

    result = run_case(case)
    sandbox_root = result.get("sandbox_root")
    actual = normalize_result(result, sandbox_root=sandbox_root)
    expected = normalize_result(golden, sandbox_root=sandbox_root)

    # exit code: always checked
    assert actual["state"]["exit_code"] == expected["state"]["exit_code"], (
        f"exit code mismatch; stderr:\n{result.get('stderr')}"
    )

    # file effects: when the case opts in (policy.check_files) and the golden
    # carries a recorded file snapshot, the post-run sandbox must match the
    # reference's byte-for-byte. This catches tools that print the right words
    # but mutate (or fail to mutate) the filesystem wrongly — invisible to a
    # stdout-only diff. Re-record with: python scripts/record_golden.py <id>.
    if case.get("policy", {}).get("check_files") and "files" in expected:
        files_diff = compare_file_effects(
            actual.get("files", []), expected.get("files", [])
        )
        if files_diff and case.get("known_divergence"):
            pytest.xfail(f"known hare divergence (files): {case['known_divergence']}\n{files_diff}")
        assert files_diff is None, f"file-effect mismatch:\n{files_diff}"

    # Structural JSON comparison: compare the result object's stable fields,
    # ignoring volatile/env-specific ones. Used for --output-format json/stream-json.
    if case.get("policy", {}).get("match") == "json_structural":
        kind = case.get("expected", {}).get("stdout_kind", "json")
        actual_obj = _find_result_obj(result["stdout"], kind)
        expected_obj = _find_result_obj(golden["stdout"], kind)
        assert actual_obj is not None, (
            f"hare produced no result JSON object; stdout:\n{result['stdout']!r}"
        )
        assert expected_obj is not None, "golden has no result JSON object"
        a, e = _stable_result(actual_obj), _stable_result(expected_obj)
        if a != e and case.get("known_divergence"):
            pytest.xfail(f"known hare divergence: {case['known_divergence']}\n  {a}\n  {e}")
        assert a == e, f"\n--- expected (reference) ---\n{e}\n--- actual (hare) ---\n{a}"
        # SDK contract: for a successful result, hare must emit every top-level
        # key Claude Code does (naming included) so consumers' parsers don't break.
        # (Error variants carry provider-specific fields like `errors`; not enforced.)
        if expected_obj.get("subtype") == "success":
            missing = set(expected_obj) - set(actual_obj)
            assert not missing, (
                f"hare's json result is missing reference keys {sorted(missing)}; "
                f"hare keys={sorted(actual_obj)}"
            )
        return

    # stdout: exact match when golden carries a 'stdout' field
    if "stdout" in expected:
        mismatch = actual["stdout"] != expected["stdout"]
        # A documented hare-vs-reference divergence: track it as xfail (keeps the
        # suite green, surfaces an xpass alert once hare is fixed) instead of
        # hiding the difference by editing the golden.
        if mismatch and case.get("known_divergence"):
            pytest.xfail(
                f"known hare divergence: {case['known_divergence']}\n"
                f"  reference: {expected['stdout']!r}\n"
                f"  hare:      {actual['stdout']!r}"
            )
        assert not mismatch, (
            f"\n--- expected (reference) ---\n{expected['stdout']!r}\n"
            f"--- actual (hare) ---\n{actual['stdout']!r}"
        )

    # substring assertions (lighter-weight contract checks)
    for needle in golden.get("stdout_contains", []):
        assert needle in result["stdout"], (
            f"stdout missing {needle!r}; got:\n{result['stdout']}"
        )
    for needle in golden.get("stderr_contains", []):
        assert needle in result["stderr"], (
            f"stderr missing {needle!r}; got:\n{result['stderr']}"
        )
