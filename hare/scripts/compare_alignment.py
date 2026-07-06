#!/usr/bin/env python3
"""Compare alignment runner outputs using case-centered policy rules."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALIGNMENT_ROOT = PROJECT_ROOT / "alignment"
CASES_ROOT = ALIGNMENT_ROOT / "cases"
if str(ALIGNMENT_ROOT) not in sys.path:
    sys.path.insert(0, str(ALIGNMENT_ROOT))

from normalize import normalize_result  # noqa: E402

WEIGHTS = {"P0": 100, "P1": 20, "P2": 5, "P3": 1}


@dataclass
class AllowDelta:
    path: str
    reason: str
    expires_at: str
    kind: str

    def is_expired(self) -> bool:
        try:
            return date.fromisoformat(self.expires_at) < date.today()
        except ValueError:
            return True


def load_jsonl_by_case(path: str) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            case_id = item.get("case_id")
            if not case_id:
                raise ValueError(f"Missing case_id in {path}")
            if case_id in results:
                raise ValueError(f"Duplicate case_id '{case_id}' in {path}")
            results[case_id] = item
    return results


def load_case_definitions(cases_dir: Path) -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for case_file in sorted(cases_dir.glob("**/case.json")):
        case = json.loads(case_file.read_text(encoding="utf-8"))
        cases[case["case_id"]] = case
    return cases


def _stringify_path(parts: list[str]) -> str:
    return ".".join(parts)


def _compare_numbers(
    path: str, left: Any, right: Any, tolerance: dict[str, Any]
) -> bool:
    rule = tolerance.get(path)
    if rule is None:
        return left == right
    if isinstance(rule, str) and rule.startswith("+/-"):
        try:
            margin = float(rule[3:])
            return abs(float(left) - float(right)) <= margin
        except ValueError:
            return False
    if isinstance(rule, str) and rule.startswith("<="):
        try:
            ceiling = float(rule[2:])
            return abs(float(left) - float(right)) <= ceiling
        except ValueError:
            return False
    return left == right


def diff_values(
    left: Any,
    right: Any,
    *,
    path: list[str],
    tolerance: dict[str, Any],
    diffs: list[dict[str, Any]],
) -> None:
    current_path = _stringify_path(path)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if not _compare_numbers(current_path, left, right, tolerance):
            diffs.append({"path": current_path, "left": left, "right": right})
        return
    if type(left) is not type(right):
        diffs.append({"path": current_path, "left": left, "right": right})
        return
    if isinstance(left, dict):
        all_keys = sorted(set(left) | set(right))
        for key in all_keys:
            if key not in left:
                diffs.append(
                    {
                        "path": _stringify_path(path + [key]),
                        "left": "<missing>",
                        "right": right[key],
                    }
                )
            elif key not in right:
                diffs.append(
                    {
                        "path": _stringify_path(path + [key]),
                        "left": left[key],
                        "right": "<missing>",
                    }
                )
            else:
                diff_values(
                    left[key],
                    right[key],
                    path=path + [key],
                    tolerance=tolerance,
                    diffs=diffs,
                )
        return
    if isinstance(left, list):
        if len(left) != len(right):
            diffs.append(
                {
                    "path": current_path,
                    "left": f"len={len(left)}",
                    "right": f"len={len(right)}",
                }
            )
        for idx, (left_item, right_item) in enumerate(zip(left, right)):
            diff_values(
                left_item,
                right_item,
                path=path + [str(idx)],
                tolerance=tolerance,
                diffs=diffs,
            )
        return
    if left != right:
        diffs.append({"path": current_path, "left": left, "right": right})


def filter_diffs(
    diffs: list[dict[str, Any]],
    *,
    allow_deltas: list[AllowDelta],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    remaining: list[dict[str, Any]] = []
    matched: list[dict[str, Any]] = []
    errors: list[str] = []

    for allow in allow_deltas:
        if allow.is_expired():
            errors.append(f"Expired allow_delta: {allow.path} ({allow.expires_at})")

    for diff in diffs:
        # Prefix match: allow "events" covers "events.0.action", etc.
        hit = next(
            (
                allow
                for allow in allow_deltas
                if allow.path == diff["path"]
                or diff["path"].startswith(allow.path + ".")
            ),
            None,
        )
        if hit:
            matched.append(
                {
                    "path": diff["path"],
                    "reason": hit.reason,
                    "expires_at": hit.expires_at,
                    "kind": hit.kind,
                }
            )
        else:
            remaining.append(diff)

    return remaining, matched, errors


def compute_weighted_score(results: list[dict[str, Any]]) -> float:
    total_weight = 0.0
    passed_weight = 0.0
    for result in results:
        if result["priority"] == "P3":
            continue
        weight = WEIGHTS[result["priority"]]
        total_weight += weight
        if result["passed"]:
            passed_weight += weight
    return passed_weight / total_weight if total_weight else 0.0


def compare_case(
    case: dict[str, Any],
    ts_result: dict[str, Any] | None,
    py_result: dict[str, Any] | None,
    *,
    py_only: bool = False,
) -> dict[str, Any]:
    priority = case["priority"]
    ignore_fields = set(case.get("policy", {}).get("ignore_fields", []))
    tolerance = dict(case.get("policy", {}).get("tolerance", {}))
    allow_deltas = [
        AllowDelta(**item) for item in case.get("policy", {}).get("allow_delta", [])
    ]

    if py_only:
        # Python-only mode: check that Python runs successfully
        if py_result is None:
            return {
                "case_id": case["case_id"],
                "priority": priority,
                "passed": False,
                "diff_count": 1,
                "diff_details": [{"path": "py", "left": "missing", "right": "missing"}],
                "matched_allow_deltas": [],
                "gate_errors": [],
            }
        if py_result.get("status") in ("ok", "skipped"):
            return {
                "case_id": case["case_id"],
                "priority": priority,
                "passed": True,
                "diff_count": 0,
                "diff_details": [],
                "matched_allow_deltas": [],
                "gate_errors": [],
            }
        # Python errored — check if allow_delta covers the error
        py_error = py_result.get("error")
        if py_error:
            for allow in allow_deltas:
                if allow.path == "error":
                    return {
                        "case_id": case["case_id"],
                        "priority": priority,
                        "passed": True,
                        "diff_count": 0,
                        "diff_details": [],
                        "matched_allow_deltas": [
                            {
                                "path": "error",
                                "reason": allow.reason,
                                "expires_at": allow.expires_at,
                                "kind": allow.kind,
                            }
                        ],
                        "gate_errors": [],
                    }
        return {
            "case_id": case["case_id"],
            "priority": priority,
            "passed": False,
            "diff_count": 1,
            "diff_details": [
                {
                    "path": "py.status",
                    "left": "ok",
                    "right": py_result.get("status", "unknown"),
                }
            ],
            "matched_allow_deltas": [],
            "gate_errors": [],
        }

    if ts_result is None or py_result is None:
        missing_side = "ts" if ts_result is None else "py"
        return {
            "case_id": case["case_id"],
            "priority": priority,
            "passed": False,
            "diff_count": 1,
            "diff_details": [
                {"path": missing_side, "left": "missing", "right": "missing"}
            ],
            "matched_allow_deltas": [],
            "gate_errors": [],
        }

    normalized_ts = normalize_result(ts_result, ignore_fields=ignore_fields)
    normalized_py = normalize_result(py_result, ignore_fields=ignore_fields)

    diffs: list[dict[str, Any]] = []
    diff_values(normalized_ts, normalized_py, path=[], tolerance=tolerance, diffs=diffs)
    filtered_diffs, matched_allow_deltas, gate_errors = filter_diffs(
        diffs, allow_deltas=allow_deltas
    )

    return {
        "case_id": case["case_id"],
        "priority": priority,
        "passed": not filtered_diffs and not gate_errors,
        "diff_count": len(filtered_diffs),
        "diff_details": filtered_diffs[:10],
        "matched_allow_deltas": matched_allow_deltas,
        "gate_errors": gate_errors,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Compare TS and Python alignment results"
    )
    parser.add_argument("--ts", required=True, help="TS runner JSONL output")
    parser.add_argument("--py", required=True, help="Python runner JSONL output")
    parser.add_argument(
        "--cases-dir", default=str(CASES_ROOT), help="alignment/cases root"
    )
    parser.add_argument("--priority", default="P0,P1,P2,P3")
    parser.add_argument("--weighted-min", type=float, default=0.999)
    parser.add_argument(
        "--py-only", action="store_true", help="Python-only mode (skip TS comparison)"
    )
    parser.add_argument("--report", default=None)
    parser.add_argument("--md", default=None)
    args = parser.parse_args()

    priorities = {item.strip() for item in args.priority.split(",") if item.strip()}
    cases = load_case_definitions(Path(args.cases_dir))
    ts_results = load_jsonl_by_case(args.ts)
    py_results = load_jsonl_by_case(args.py)

    # Auto-detect Python-only mode: TS file is empty or /dev/null
    py_only = args.py_only or args.ts == "/dev/null" or not ts_results

    results: list[dict[str, Any]] = []
    for case_id, case in sorted(cases.items()):
        if case["priority"] not in priorities:
            continue
        result = compare_case(
            case, ts_results.get(case_id), py_results.get(case_id), py_only=py_only
        )
        results.append(result)

    weighted = compute_weighted_score(results)
    p0_failures = [
        result
        for result in results
        if result["priority"] == "P0" and not result["passed"]
    ]
    p1_failures = [
        result
        for result in results
        if result["priority"] == "P1" and not result["passed"]
    ]
    gate_errors = [error for result in results for error in result["gate_errors"]]

    if weighted < args.weighted_min:
        gate_errors.append(f"Weighted score {weighted:.4f} < {args.weighted_min}")
    for result in p0_failures + p1_failures:
        gate_errors.append(
            f"HARD FAIL: {result['case_id']} ({result['priority']}) — {result['diff_count']} diffs"
        )

    print("Alignment Comparison Report")
    print("=" * 60)
    print(f"Total cases compared: {len(results)}")
    print(f"P0 failures: {len(p0_failures)}")
    print(f"P1 failures: {len(p1_failures)}")
    print(f"Weighted score: {weighted:.4f}")
    print(f"Gates: {'PASS' if not gate_errors else 'FAIL'}")

    failures = [result for result in results if not result["passed"]]
    if failures:
        print("\nFailures:")
        for result in failures:
            print(f"  [{result['priority']}] {result['case_id']}")
            for diff in result["diff_details"][:3]:
                print(f"    - {diff['path']}: {diff['left']} != {diff['right']}")
            for error in result["gate_errors"]:
                print(f"    - {error}")

    if gate_errors:
        print("\nGate Errors:")
        for error in gate_errors:
            print(f"  - {error}")

    report = {
        "cases": results,
        "summary": {
            "total": len(results),
            "weighted": weighted,
            "p0_failures": len(p0_failures),
            "p1_failures": len(p1_failures),
            "gate_errors": gate_errors,
            "gates_pass": not gate_errors,
        },
    }

    if args.report:
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if args.md:
        lines = [
            "# Alignment Report",
            "",
            f"**Weighted score**: {weighted:.4f}",
            f"**Gates**: {'PASS' if not gate_errors else 'FAIL'}",
            "",
            "| Priority | Case | Passed | Diffs |",
            "|---|---|---:|---:|",
        ]
        for result in results:
            lines.append(
                f"| {result['priority']} | {result['case_id']} | "
                f"{'yes' if result['passed'] else 'no'} | {result['diff_count']} |"
            )
        Path(args.md).write_text("\n".join(lines) + "\n", encoding="utf-8")

    return 0 if not gate_errors else 1


if __name__ == "__main__":
    sys.exit(main())
