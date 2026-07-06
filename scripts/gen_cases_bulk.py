#!/usr/bin/env python3
"""Bulk case generator for alignment B — creates case.json files with varied params.

Adds cases for existing module_func entries and newly registered functions.
"""

import json
import os
import secrets
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = PROJECT_ROOT / "alignment" / "cases"

def make_case(case_id: str, priority: str, module_func: str,
              module_kwargs: dict, description: str = "",
              modules: list = None,
              blocking: bool = False) -> dict:
    if modules is None:
        modules = ["various.ts", "hare/various.py"]
    return {
        "case_id": case_id,
        "priority": priority,
        "modules": modules,
        "description": description or case_id,
        "entrypoint": {
            "kind": "module",
            "module_func": module_func,
            "module_kwargs": module_kwargs,
        },
        "mocks": {"model": {}},
        "expected": {"exit_code": 0},
        "policy": {
            "ignore_fields": [],
            "tolerance": {},
            "blocking": blocking,
        },
    }

def write_case(case: dict):
    priority = case["priority"]
    # case_id format: module.func.variant
    parts = case["case_id"].split(".")
    # Use first two parts as dir structure, rest as file name
    if len(parts) >= 3:
        module_name = parts[0]
        func_name = parts[1]
        dir_path = CASES_DIR / priority / "module" / f"{module_name}_{func_name}_{parts[2]}"
    else:
        dir_path = CASES_DIR / priority / "module" / case["case_id"].replace(".", "_")
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "case.json").write_text(
        json.dumps(case, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

def generate_all():
    count = 0

    # ── permission.match_wildcard — P2 (more patterns) ──
    wildcard_patterns = [
        ("a/b/c", "a/b/c", False),  # exact match
        ("*", "anything", True),
        ("a/*", "a/b", True),
        ("a/*", "a/b/c", False),  # single-segment only
        ("a/*/c", "a/b/c", True),
        ("a/*/c", "a/b/d", False),
        ("a/*/c", "a/b/x/c", False),
        ("a/**", "a/b", True),  # double star
        ("a/**", "a/b/c", True),
        ("a/**", "b/c", False),
        ("a/b/**", "a/b/c", True),
        ("a/b/**", "a/b/c/d/e", True),
        ("**/c", "a/b/c", True),
        ("**/c", "x/c", True),
        ("**/c", "c", True),
        ("*.ts", "file.ts", True),
        ("*.ts", "file.js", False),
        ("?at", "cat", True),
        ("?at", "bat", True),
        ("?at", "at", False),
        ("[abc]at", "cat", True),
        ("[abc]at", "bat", True),
        ("[abc]at", "zat", False),
        ("[!abc]at", "zat", True),
        ("[!abc]at", "cat", False),
        ("a/b/**/c", "a/b/x/y/z/c", True),
        ("a/b/**/c", "a/b/c", True),
        ("a/b/**/c", "a/x/c", False),
        ("git", "git", True),  # exact tool match
        ("git *", "git status", True),
        ("git *", "npm install", False),
        ("Bash(git:*)", "git status", False),  # tool-prefixed
        # Edge cases
        ("", "", True),
        ("", "x", False),
        ("****", "x", True),
        ("a/*/b/*/c", "a/x/b/y/c", True),
        ("a/*/b/*/c", "a/x/b/c", False),
        # Real-world patterns
        ("npm *", "npm install", True),
        ("npm *", "npm run build", True),
        ("npm *", "node server.js", False),
        ("python *", "python script.py", True),
        ("python *", "python3 script.py", False),
    ]
    for pattern, command, expected_match in wildcard_patterns:
        variant = f"p{pattern.replace('/', '_').replace('*', 'S').replace('?', 'Q').replace('[', 'L').replace(']', 'R').replace('!', 'N')[:50]}_c{command.replace('/', '_')[:30]}"
        variant = "".join(c if c.isalnum() or c in "_-" else "_" for c in variant)[:60]
        case_id = f"permission.match_wildcard.{variant}"
        write_case(make_case(case_id, "P2", "permission.match_wildcard",
                              {"pattern": pattern, "command": command},
                              f"matchWildcard pattern='{pattern}' command='{command}'",
                              blocking=True))
        count += 1

    # ── hooks.is_blocked_address — P1/P2 (more addresses) ──
    # P1: security-critical
    p1_addresses = [
        ("127.0.0.1", True), ("::1", True), ("localhost", True),
        ("0.0.0.0", True), ("10.0.0.1", True), ("172.16.0.1", True),
        ("192.168.1.1", True), ("169.254.1.1", True),
        ("[::1]", True), ("0", True),
        ("[::]", True), ("[::ffff:127.0.0.1]", True),
        ("[::ffff:0:0]", True), ("127.0.0.1:8080", True),
    ]
    for addr, blocked in p1_addresses:
        variant = addr.replace(".", "_").replace(":", "_").replace("[", "").replace("]", "")[:50]
        case_id = f"hooks.is_blocked.{variant}"
        write_case(make_case(case_id, "P1", "hooks.is_blocked_address",
                              {"address": addr},
                              f"isBlockedAddress '{addr}'",
                              blocking=True))
        count += 1

    p2_addresses = [
        ("8.8.8.8", False), ("1.1.1.1", False), ("google.com", False),
        ("example.com", False), ("api.example.com", False),
        ("169.254.169.254", True),  # AWS metadata
        ("metadata.google.internal", True),  # GCP metadata
        ("[::ffff:169.254.169.254]", True),
        ("192.0.2.1", True),  # TEST-NET
        ("198.51.100.1", True),  # TEST-NET-2
        ("203.0.113.1", True),  # TEST-NET-3
        ("[fc00::1]", True),  # Unique local
        ("[fd00::1]", True),  # Unique local
        ("[fe80::1]", True),  # Link-local
        ("[ff00::1]", True),  # Multicast
        ("224.0.0.1", True),  # Multicast
        ("240.0.0.1", True),  # Reserved
        ("[::1]:3000", True),
        ("127.0.0.1.nip.io", True),  # nip.io bypass
        ("localtest.me", True),  # resolves to 127.0.0.1
        ("broadcasthost", True),
        ("[0:0:0:0:0:0:0:0]", True),
        ("[::ffff:8.8.8.8]", False),
        ("api.github.com", False),
        ("10.0.0.1:443", True),
        ("192.168.0.1:22", True),
    ]
    for addr, blocked in p2_addresses:
        variant = addr.replace(".", "_").replace(":", "_").replace("[", "").replace("]", "")[:50]
        variant = "".join(c if c.isalnum() or c in "_-" else "_" for c in variant)[:50]
        case_id = f"hooks.is_blocked.{variant}"
        write_case(make_case(case_id, "P2", "hooks.is_blocked_address",
                              {"address": addr},
                              f"isBlockedAddress '{addr}'",
                              blocking=True))
        count += 1

    # ── token_budget.check — P2 (more scenarios) ──
    budget_cases = [
        (None, 200000, 100, "continue"),  # small usage
        (None, 200000, 50000, "continue"),
        (None, 200000, 100000, "continue"),
        (None, 200000, 150000, "continue"),
        (None, 200000, 190000, "continue"),
        (None, 5000, 100, "continue"),
        (None, 5000, 4500, "continue"),
        (None, 5000, 5000, "stop"),
        (None, 5000, 5500, "stop"),
        (None, 1000, 500, "continue"),
        (None, 1000, 900, "continue"),
        (None, 1000, 1000, "stop"),
        (None, 100000, 80000, "continue"),
        (None, 100000, 95000, "continue"),
        (None, 100000, 100000, "stop"),
        ("agent_x", 50000, 25000, "continue"),
        ("agent_x", 50000, 45000, "continue"),
        ("agent_x", 50000, 50000, "stop"),
        (None, 10000, 1, "continue"),
        (None, 10000, 9999, "continue"),
    ]
    for agent, budget, tokens, action in budget_cases:
        variant = f"a{str(agent)[:8]}_b{budget}_t{tokens}"
        case_id = f"token_budget.check.{variant}"
        write_case(make_case(case_id, "P2", "token_budget.check",
                              {"agent_id": agent, "budget": budget, "tokens": tokens},
                              f"checkTokenBudget agent={agent} budget={budget} tokens={tokens}",
                              blocking=True))
        count += 1

    # ── history.format_pasted_ref — more edge cases ──
    for id_val, num_lines in [
        (0, 0), (1, 0), (0, 1), (1, 1), (5, 3), (10, 10),
        (100, 100), (999, 999), (50, 0), (0, 50),
        (7, 13), (42, 7), (256, 128), (1024, 512),
    ]:
        case_id = f"history.format_pasted_ref.id{id_val}_n{num_lines}"
        write_case(make_case(case_id, "P2", "history.format_pasted_ref",
                              {"id": id_val, "num_lines": num_lines},
                              f"formatPastedTextRef id={id_val} numLines={num_lines}"))
        count += 1

    # ── history.format_image_ref — more edge cases ──
    for id_val in [-1, 0, 1, 2, 10, 50, 99, 100, 500, 999, 1000]:
        case_id = f"history.format_image_ref.id{id_val}"
        write_case(make_case(case_id, "P2", "history.format_image_ref",
                              {"id": id_val},
                              f"formatImageRef id={id_val}"))
        count += 1

    # ── history.parse_references — more edge cases ──
    ref_texts = [
        "no references here",
        "{paste #1}",
        "{paste #1} and {paste #2}",
        "{image_ref}",
        "text with {paste #42} inline",
        "{paste #1}{paste #2}",
        "prefix {paste #99} suffix",
        "{paste #1}\n{paste #2}\n{paste #3}",
        "{paste #1} {paste #1}",  # duplicate
        "no match {paste } here",
        "{paste #} empty id",
        "escaped \\{paste #1\\}",
        "{paste #0} zero id",
        "{paste #999999} large id",
    ]
    for i, text in enumerate(ref_texts):
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in text[:30])
        case_id = f"history.parse_references.{safe}_{i}"
        write_case(make_case(case_id, "P2", "history.parse_references",
                              {"input_text": text},
                              f"parseReferences '{text[:50]}'"))
        count += 1

    # ── permission.has_wildcards — new function ──
    wc_patterns = [
        ("*", True), ("a*", True), ("*b", True), ("a*b", True),
        ("?", True), ("a?b", True), ("[abc]", True), ("[!abc]", True),
        ("exact", False), ("simple/path", False), ("", False),
        ("no_wildcards_here", False), ("just.plain.text", False),
        ("has*star", True), ("has?question", True),
        ("a/b/c", False), ("a/b/*", True), ("a/*/c", True),
        ("**", True), ("**/x", True), ("x/**", True),
        ("[a-z]", True), ("file.{js,ts}", False),  # braces aren't wildcards
        ("mixed*and?chars", True), ("****", True),
        ("?", True), ("*?", True), ("?*", True),
        ("[!a-z]", True), ("**/**", True),
        ("1*2?3[4]5", True), ("plain", False),
    ]
    for pattern, expected in wc_patterns:
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in pattern)[:50] or "empty"
        case_id = f"permission.has_wildcards.{safe}"
        write_case(make_case(case_id, "P2", "permission.has_wildcards",
                              {"pattern": pattern},
                              f"hasWildcards '{pattern}'",
                              blocking=True))
        count += 1

    # ── permission.escape_rule — new function ──
    escape_cases = [
        ("hello world", "hello world"),  # no special chars
        ("hello\\ world", "hello\\\\ world"),
        ("hello:world", "hello:world"),
        ("path/to/file", "path/to/file"),
        ("space here", "space here"),
        ("backslash\\\\test", "backslash\\\\\\\\test"),
        ("quote\"test", "quote\\\"test"),
        ("", ""),
        ("    ", "    "),
        ("a\nb", "a\nb"),
        ("a\tb", "a\tb"),
        ("special:chars*here?", "special:chars*here?"),
        ("normal_text_123", "normal_text_123"),
        ("UPPERCASE", "UPPERCASE"),
        ("c:\\path\\to\\file", "c:\\\\path\\\\to\\\\file"),
        ("already\\\\escaped", "already\\\\\\\\escaped"),
        ("mixed\\\\and\\not", "mixed\\\\\\\\and\\\\not"),
        ("single\\backslash", "single\\\\backslash"),
        ("double\\\\backslash", "double\\\\\\\\backslash"),
        ("triple\\\\\\backslash", "triple\\\\\\\\\\\\backslash"),
    ]
    for content, expected_escaped in escape_cases:
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in content[:40]) or "empty"
        case_id = f"permission.escape_rule.{safe}"
        write_case(make_case(case_id, "P2", "permission.escape_rule",
                              {"content": content},
                              f"escapeRuleContent '{content[:40]}'",
                              blocking=True))
        count += 1

    # ── permission.unescape_rule — new function ──
    unescape_cases = [
        ("hello world", "hello world"),
        ("hello\\\\ world", "hello\\ world"),
        ("hello:world", "hello:world"),
        ("c:\\\\path\\\\to\\\\file", "c:\\path\\to\\file"),
        ("", ""),
        ("no_backslash", "no_backslash"),
        ("single\\backslash", "single\\backslash"),
        ("double\\\\backslash", "double\\backslash"),
        ("triple\\\\\\backslash", "triple\\\\backslash"),
        ("quad\\\\\\\\backslash", "quad\\\\backslash"),
        ("mixed\\\\and\\not", "mixed\\and\\not"),
        ("ends_with\\\\", "ends_with\\"),
        ("\\\\starts_with", "\\starts_with"),
        ("only\\\\double", "only\\double"),
        ("a\\\\b\\\\c", "a\\b\\c"),
        ("escape\\\\\\\\sequences", "escape\\\\sequences"),
        ("\\\\", "\\"),
        ("\\\\\\\\", "\\\\"),
        ("text\\with\\\\mixed", "text\\with\\mixed"),
        ("path\\\\to\\\\file", "path\\to\\file"),
    ]
    for content, expected_unescaped in unescape_cases:
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in content[:40]) or "empty"
        case_id = f"permission.unescape_rule.{safe}"
        write_case(make_case(case_id, "P2", "permission.unescape_rule",
                              {"content": content},
                              f"unescapeRuleContent '{content[:40]}'",
                              blocking=True))
        count += 1

    # ── permission.parse_rule — more P1/P2 rules ──
    parse_rules = [
        "allow git *",
        "allow npm *",
        "deny rm *",
        "allow Bash(git:*)",
        "deny Bash(rm:*)",
        "allow python *",
        "allow node *",
        "deny curl *",
        "allow Bash(docker:*)",
        "allow Bash(kubectl:*)",
        "allow MCP(test_server:mcp__test__*)",
        "allow MCP(server:mcp__tool__*)",
    ]
    for rule_str in parse_rules:
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in rule_str)[:60]
        case_id = f"permission.parse_rule.{safe}"
        write_case(make_case(case_id, "P1", "permission.parse_rule",
                              {"rule_string": rule_str},
                              f"parseRule '{rule_str}'",
                              blocking=True))
        count += 1

    print(f"Generated {count} new cases")
    return count

if __name__ == "__main__":
    generate_all()
