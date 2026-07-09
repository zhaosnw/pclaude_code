# Alignment Definition — Hare ↔ TS Reference Implementation

> Status note (2026-07-07): this file defines the legacy Phase1 / py-only alignment gate.
> It is not the current repo-wide status page and should not be read as the latest overall alignment result.
> For the latest verified repo status, use `REVIEW_2026-07-02.md` and `docs/alignment-status/2026-07-07.md`.

## 99.9% Alignment Formula

```
weighted_score = (Σ case.weight × case.passed) / (Σ case.weight)

weight(P0)=100, weight(P1)=20, weight(P2)=5, weight(P3)=1
(P3 excluded from denominator by default)

release_ready ⇔
  P0_pass == 100%  AND
  P1_pass == 100%  AND
  weighted_score >= 0.999  AND
  P0_P1_line_coverage >= 0.90  AND
  P0_P1_branch_coverage >= 0.80  AND
  no_regression_vs_last_release  AND
  zero stubs in P0_P1 (NotImplementedError) AND
  zero high-severity+high-confidence bandit findings
```

## Hard Rules

- Any P0 or P1 case failure → CI failure (regardless of weighted score)
- Cannot mask P0/P1 regression with easy P2 cases
- Allowlist entries must have `reason` + `expires_at`

## Priority Definitions

| Priority | Meaning | CI Gate | Stub Gate |
|----------|---------|---------|-----------|
| P0 | query loop, tool execution, permission, message normalization, CLI, config, MCP stdio, session, abort | 100% pass | NIE=0, TODO=0 |
| P1 | commands, hooks, plugins, compact, token budget, cost, env/settings, file validation | 100% pass | NIE=0, TODO≤10 |
| P2 | UI/Ink, bridge/remote, analytics, voice, LSP, team/swarm | advisory | NIE≤20, TODO≤200 |
| P3 | ANT-only, platform-specific, deprecated | excluded | advisory only |

## Replay

```bash
# Replay a single alignment case
make replay CASE=cli.print.stream_json_event_order

# Replay all P0 cases
make alignment-quick

# Full comparison (P0+P1)
make alignment-full
```

## Path Conventions

- `legacy_alignment/cases/<priority>/<module>/<case_id>/case.json`
- TS oracle: `recovered-from-cli-js-map/alignment-harness/runner.ts`
- Python oracle: `hare/scripts/alignment_runner.py`
- Compare: `hare/scripts/compare_alignment.py`
- Normalize: `legacy_alignment/normalize.py`
