#!/usr/bin/env python3
"""Gate P0/P1 module coverage using canonical repo-root paths."""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALIGNMENT_DATA = PROJECT_ROOT / "legacy_alignment" / "alignment_data.json"


def load_p0p1_modules(require_done: bool = True) -> set[str]:
    data = json.loads(ALIGNMENT_DATA.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for entry in data.get("rows", []):
        if not isinstance(entry, dict):
            continue
        if entry.get("priority") not in {"P0", "P1"}:
            continue
        # Skip excluded entries
        if entry.get("excluded"):
            continue
        # Only count done-status entries (exclude renamed?, partial, stub, etc.)
        # This focuses coverage on files that are actually ported.
        if require_done and entry.get("status") != "done":
            continue
        py_path = str(entry.get("py", ""))
        if py_path.startswith("hare/") and " | " not in py_path:
            modules.add(py_path)
    return modules


def _parse_condition_coverage(raw: str) -> tuple[int, int] | None:
    # Cobertura usually emits "50% (1/2)"
    if "(" not in raw or "/" not in raw or ")" not in raw:
        return None
    try:
        fraction = raw.split("(", 1)[1].split(")", 1)[0]
        covered, total = fraction.split("/", 1)
        return int(covered), int(total)
    except (ValueError, IndexError):
        return None


def parse_coverage(coverage_xml: Path) -> tuple[dict[str, dict[str, int]], bool]:
    tree = ET.parse(coverage_xml)
    root = tree.getroot()
    result: dict[str, dict[str, int]] = {}
    branch_data_present = False

    for cls in root.findall(".//class"):
        filename = cls.get("filename", "").replace("\\", "/")
        if not filename:
            continue
        repo_relative = filename
        # Normalize coverage paths to canonical repo-root-relative form:
        #   hare/<module>.py  (what alignment_data.json rows use)
        if not repo_relative.startswith("hare/"):
            repo_relative = f"hare/{repo_relative.lstrip('./')}"

        line_count = 0
        line_covered = 0
        branch_total = 0
        branch_covered = 0
        lines_node = cls.find("lines")
        if lines_node is not None:
            for line in lines_node.findall("line"):
                line_count += 1
                if int(line.get("hits", "0")) > 0:
                    line_covered += 1
                if line.get("branch") == "true":
                    parsed = _parse_condition_coverage(
                        line.get("condition-coverage", "")
                    )
                    if parsed:
                        covered, total = parsed
                        branch_covered += covered
                        branch_total += total
                        branch_data_present = True

        result[repo_relative] = {
            "lines": line_count,
            "covered": line_covered,
            "branches": branch_total,
            "branch_covered": branch_covered,
        }

    return result, branch_data_present


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate P0/P1 coverage")
    parser.add_argument("--coverage-xml", default="coverage.xml")
    parser.add_argument("--min-line", type=float, default=0.0)
    parser.add_argument("--min-branch", type=float, default=0.0)
    parser.add_argument("--fail-on-unmatched", action="store_true")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    coverage_xml = Path(args.coverage_xml)
    if not coverage_xml.exists():
        print(f"ERROR: coverage.xml not found at {coverage_xml}", file=sys.stderr)
        return 2

    p0p1_modules = load_p0p1_modules()
    if not p0p1_modules:
        print("WARNING: no P0/P1 modules found; skipping", file=sys.stderr)
        return 0

    coverage, branch_data_present = parse_coverage(coverage_xml)

    line_total = 0
    line_covered = 0
    branch_total = 0
    branch_covered = 0
    matched_files: list[str] = []
    unmatched_files: list[str] = []

    for module in sorted(p0p1_modules):
        stats = coverage.get(module)
        if stats is None:
            unmatched_files.append(module)
            continue
        matched_files.append(module)
        line_total += stats["lines"]
        line_covered += stats["covered"]
        branch_total += stats["branches"]
        branch_covered += stats["branch_covered"]

    line_pct = (line_covered / line_total * 100) if line_total else 0.0
    branch_pct = (branch_covered / branch_total * 100) if branch_total else 0.0
    branch_gate_skipped = not branch_data_present

    report = {
        "p0p1_line_coverage": round(line_pct, 1),
        "p0p1_branch_coverage": round(branch_pct, 1) if branch_data_present else None,
        "branch_data_present": branch_data_present,
        "matched_files": matched_files,
        "unmatched_files": unmatched_files,
        "gate_line_pass": line_pct >= args.min_line * 100,
        "gate_branch_pass": branch_gate_skipped or branch_pct >= args.min_branch * 100,
    }

    if args.output:
        Path(args.output).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(
        f"P0/P1 Line Coverage:   {line_pct:.1f}% (min: {args.min_line * 100:.0f}%) {'PASS' if report['gate_line_pass'] else 'FAIL'}"
    )
    if branch_gate_skipped:
        print(
            "P0/P1 Branch Coverage: unavailable in coverage.xml (branch gate skipped)"
        )
    else:
        print(
            f"P0/P1 Branch Coverage: {branch_pct:.1f}% (min: {args.min_branch * 100:.0f}%) {'PASS' if report['gate_branch_pass'] else 'FAIL'}"
        )
    print(f"Matched files: {len(matched_files)}, Unmatched: {len(unmatched_files)}")

    if unmatched_files:
        print("Unmatched P0/P1 files:")
        for module in unmatched_files[:20]:
            print(f"  - {module}")

    if not report["gate_line_pass"]:
        return 1
    if not report["gate_branch_pass"]:
        return 1
    if unmatched_files and args.fail_on_unmatched:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
