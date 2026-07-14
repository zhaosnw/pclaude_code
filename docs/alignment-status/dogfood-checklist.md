# Hare Dogfood Checklist

Run this checklist after every one or two completed alignment axes. Golden
cases prove differential behavior; these scenarios prove that the CLI remains
usable as a code agent. Run each scenario in an isolated temporary git
repository with an isolated `HARE_CONFIG_DIR` / `CLAUDE_CONFIG_DIR`.

| Scenario | Required behavior | Evidence to record |
|---|---|---|
| File change | `python -m hare -p` reads, edits, and verifies a project file. | Final file content and CLI result. |
| Resumed task | First turn creates a file; `--resume` second turn changes it. | Session ID, final file content, and both exits. |
| Permission denial | A deny rule blocks a requested tool with a clear user-facing message. | Rule, command, stdout/stderr, and absence of the mutation. |
| MCP tool | The seeded `mcp_echo_server.py` connects and completes one `echo` call. | Config, tool result, and process exit. |

Run with `make dogfood` (exits non-zero on any failure).

## Known Flake

The MCP scenario hung once (120s timeout, not slowness) and then passed four
consecutive runs. Suspected race in stdio server startup. If it recurs, capture
the hung process's stack before killing it rather than re-running.

## Recording Rules

- Do not use real network services or a real user configuration directory.
- A failure must create or update a `hare/alignment/cases/**/case.json` item,
  or be logged as a named issue with its reproduction command.
- Record each run in the current date's alignment status note; do not mark a
  scenario complete based solely on unit or golden tests.
