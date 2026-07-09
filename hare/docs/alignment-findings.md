# Alignment findings (hare ↔ Claude Code)

> Status note (2026-07-07): this file is a bug-gap ledger, not the current repo-status snapshot.
> For the latest verified status, use `REVIEW_2026-07-02.md` and `docs/alignment-status/2026-07-07.md`.
> Any historical pass counts or dates mentioned below should be read as point-in-time evidence, not current totals.

Bugs/gaps surfaced by the E2E differential suite. Output-side bugs were fixed in
place; request-side gaps that need feature work are tracked here.

## Fixed (output-side, via fixture-replay differential)

| # | Finding | Fix |
|---|---|---|
| 1 | print mode emitted a spurious **leading newline** | `hare/main.py` result render guard |
| 2 | `--output-format json/stream-json` **ignored** (emitted text) | implemented in `_run_print_mode` |
| 3 | `usage` serialized as `"NonNullableUsage(...)"` string | `_to_jsonable` (dataclass→dict) |
| 4 | error results (`error_max_turns`/`max_budget`) dropped `stop_reason` | `query_engine.py` |
| 5 | empty result printed `''` (reference prints `'\n'`) | dropped truthiness guard |
| 6 | result text **whitespace-stripped** (`get_content_text().strip()`) | `query_engine` uses `extract_text_content` |
| 7 | json result missing reference key contract (`modelUsage` naming + `api_error_status`/`ttft_ms`/`time_to_request_ms`/`terminal_reason`/`fast_mode_state`) | `_align_result_schema` |
| 8 | **no default system prompt** — engine sent 0 system blocks (request-side) | wired `get_system_prompt()` into `QueryEngine`, split on cache boundary |
| 9 | `-p ""` (empty print prompt) dropped into the **interactive REPL** (`prompt or …` treated "" as falsy); Claude Code stays non-interactive | print-mode detection uses `parsed.print_mode is not None` (found via live stress testing) |
| 10 | empty/whitespace `-p` was **sent to the model** (empty → 13 turns; `"   "` → long run / 100s timeout); Claude Code rejects it | print mode errors cleanly (exit 1) before any model call when `prompt.strip()` is empty (found via live stress) |
| 11 | **piped stdin ignored** — `echo "x" \| hare` dropped to the REPL instead of reading stdin as the prompt; `… \| hare -p ""` errored while claiming stdin was accepted | when stdin is not a TTY, read it as the prompt and run non-interactively (found via deep live stress) |

### Live-stress coverage (real DeepSeek) — clean paths
Two stress sweeps + adversarial fixtures confirmed these handle real-model output
without crash/REPL/exception: tool loops (Read/Bash/Write/Edit/Glob/Grep/LS),
**Agent/Task subagent spawn**, multi-tool chains, stream-json with tools,
large (~150KB) tool output, rapid sequential invocations, default-permission
mode, unicode/CJK/emoji, no-tool tasks, and malformed model output (unknown tool,
missing required tool input, empty content). The only defects found were the two
CLI input-edge bugs above (#10, #11) — the core agentic loop, tools, and output
are solid against both fixtures and the live model.

### Live-stress observations that are NOT hare defects (verified)
- `--max-turns N` hitting the limit returns `error_max_turns` with `is_error:true` and a **non-zero exit** — correct (it's an error condition), matches the reference.
- result `modelUsage` is keyed by the model name the **API response** returns (DeepSeek returns a `claude-sonnet-*` label), not by `ANTHROPIC_MODEL`. hare DOES honor `ANTHROPIC_MODEL` — the request payload's `model` is `deepseek-v4-pro[1m]` (verified via in-process capture). So the label reflects DeepSeek's response, not a hare bug.

## Open — request-side reproduction gap (needs feature work)

### Tool registry incompleteness (found 2026-06-14, request-side analysis)

hare advertises **14** tools; the 2.1.88 reference (`recovered-from-cli-js-map/
src/tools.ts` `getAllBaseTools()`) has a larger unconditional core. Output-only
testing can't see this — advertised tools are part of the *request*.

**hare exposes:** Agent, Bash, Edit, ExitPlanMode, Glob, Grep, NotebookEdit, Read, Skill, TodoWrite, ToolSearch, WebFetch, WebSearch, Write

**Originally missing vs 2.1.88 core (5):** `AskUserQuestion`, `EnterPlanMode`, `TaskOutput`, `TaskStop`, `SendMessage`

**FIXED 2026-06-14 (5/5 — gap closed):** all five were *implemented* under
`hare/tools_impl/` but never registered in `get_all_base_tools`. Registered
`AskUserQuestion`, `EnterPlanMode`, `TaskOutput`, `TaskStop`, and `SendMessage`
(the last is a class-singleton tool that had been mis-registered via the
function-module wrapper, so it never worked — now imported directly). hare now
exposes the full 2.1.88 core (19 tools incl. ToolSearch). The subagent filter
still drops the main-thread-only tools for sub-agents. Verified: unit 1058 /
alignment 1585 / e2e 65 green. `KNOWN_MISSING` is now empty.

Notes:
- `Agent` is correctly named in 2.1.88 (renamed to `Task` only in 2.1.165, the locally-installed `claude`), so hare's `Agent` is version-correct.
- Conditional tools (Task* under todoV2, EnterWorktree/ExitWorktree under worktree mode, Config/Tungsten for ant builds) are excluded from the core comparison.
- Pinned by `tests/e2e/test_tool_registry_parity.py` (shrink `KNOWN_MISSING` as tools land).

**Why it matters:** identical advertised tools → identical model behavior. Each
missing tool is a real implementation task, out of scope for the alignment suite.

### ~~No default system prompt~~ → FIXED (2026-06-14)

Was: `hare/query_engine.py` built the request `system_prompt` from ONLY
`custom_system_prompt` + `append_system_prompt` (a `# simplified` stub), so a
plain `python -m hare -p "..."` sent an **empty** system prompt while the
reference sends several blocks. Output-fixture testing can't see this.

**Fix:** `QueryEngine.submit_message` now calls the already-present
`get_system_prompt()` (identity/system/doing-tasks/tools/git-safety/environment/…
sections) and splits on `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` into cache-scoped blocks.
A plain invocation now sends 2 blocks (static identity-led prefix + dynamic
environment), custom/append folded in as sections. Verified: full unit (1058),
alignment (1585) and e2e (46) suites green — output unchanged (fixtures), request
now populated. Asserted by `test_request_side_alignment.py::test_default_system_prompt_is_assembled_and_sent`.

### Request envelope (captured 2026-06-14)

hare's request envelope broadly matches the reference: same core keys (`model`,
`messages`, `system`, `tools`, `max_tokens`, `stream`, `metadata`, `thinking`)
and the same tool-object shape (`{name, description, input_schema}`). Differences
(mostly version drift vs the locally-installed 2.1.165): reference adds
`context_management` + `output_config`; hare emits `betas`/`temperature`/
`tool_choice`/`stop_sequences` explicitly; cache_control placement differs
(hare on messages[0], reference on system/tools). Captured bodies:
`/tmp/claude_request.json`, `/tmp/hare_request.json` (regenerate as needed).

## Tool input-schema audit vs 2.1.88 source (2026-06-14)

Field-level compare of every shared tool's `input_schema` (param set + required)
between hare and `recovered-from-cli-js-map/src/tools/*` (2.1.88). All findings
verified against source. **7 tools already exact:** Edit, Write, Glob, WebSearch,
Skill, TaskStop, ToolSearch.

**Fixed (schema + impl, verified):**
- `WebFetch`: `prompt` now required (2.1.88: `url`+`prompt` both required).
- `Agent`: `description` now required (2.1.88: `description`+`prompt`).
- `Grep`: added `context` (canonical alias for `-C`); impl accepts either → param set matches 2.1.88.
- `NotebookEdit`: rewritten to 2.1.88's model — `cell_id` + `new_source` + `edit_mode`(replace/insert/delete) + `cell_type` (was `cell_index`/`old_string`/`new_string`). Cells located by id; unit-tested.
- `Read`: added `pages` (PDF page-range, e.g. "1-5"); wired to the existing PDF reader → param set matches 2.1.88.

**Verified already-aligned (agent over-reported — corrected):**
- `AskUserQuestion`: real 2.1.88 inputSchema is `z.strictObject({ questions })` only; the `answers`/`annotations`/`metadata` are a separate permission-component/output schema, NOT tool input. hare matches.

**Implemented (feature work, 2026-06-14):**
- `Bash` `run_in_background` + `TaskOutput` `block`/`timeout` + `TaskStop` for
  background shells — full background-shell lifecycle on the existing task
  registry (run → read(block) → stop). Tested.

**Subsystems implemented (2026-06-15) — were "remaining", now closed:**
- `Bash` `dangerously_disable_sandbox` + **sandbox execution** — wired. BashTool
  now applies a real macOS seatbelt (sandbox-exec) write-restriction wrapper when
  `SandboxManager.is_sandboxing_enabled()`; `should_use_sandbox_for_input` mirrors
  TS `shouldUseSandbox` (disable escape hatch + user excludedCommands). Default
  OFF → Bash path unchanged; Linux bubblewrap not ported (fails open). See
  `tests/test_sandbox_execution.py`.
- `Agent` multi-agent/isolation params (`name`,`team_name`,`mode`,`isolation`)
  — **added (prior note was WRONG, verified against source)**. 2.1.88's
  `fullInputSchema` ALWAYS merges name/team_name/mode + isolation; the swarm
  feature is enforced at *call time* (`team_name && !isAgentSwarmsEnabled()` →
  error), NOT by hiding the schema. Only `cwd` is gated (KAIROS/ant). hare now
  advertises all four (+`cwd` under KAIROS) and rejects team spawns when swarms
  are disabled ("Agent Teams is not yet available on your plan."). Full teammate
  spawning (tmux/iterm backends, mailbox IPC) remains a large, env-specific,
  off-by-default subsystem — hare has ~1.1k LOC of skeletons. See
  `tests/test_agent_swarm_params.py`.
- System-prompt `memory` section — **implemented**: `load_memory_prompt()` /
  `build_memory_lines()` (port of 2.1.88 memdir.ts) wired into `get_system_prompt`
  after session_guidance. Auto-memory is ON by default, so this is now in the
  default system prompt. See `tests/test_memory_prompt.py`. (`mcp_instructions` /
  `scratchpad` sections still pending — separate subsystems.)

**Architectural difference resolved (2026-06-15):**
- `ExitPlanMode` — **now uses plan-mode disk storage** (was: echoed the passed
  `plan`, marked required). call() writes a supplied plan through `save_plan` and
  reads from disk (`get_plan`) when omitted; `plan` is now optional; the result
  surfaces the saved file path. Matches 2.1.88's disk-backed architecture. See
  `tests/test_exit_plan_mode_disk.py`. `allowed_prompts` (snake) kept as hare's
  convention.

**hare extras (intentional — not changing):**
- hare extras (`TodoWrite.merge`, `SendMessage.color`, `EnterPlanMode.plan`, `TaskOutput.max_lines`) — hare enhancements; kept (hare is a superset).

## Adversarial verification pass (2026-06-15)

A workflow of 4 independent skeptics re-checked the 4 subsystems above against
the TS source. Real findings were fixed (TDD, full suite green); honest scope
limits are documented in code.

**Fixed:**
- *memory* — `TYPES_SECTION_INDIVIDUAL` + `MEMORY_FRONTMATTER_EXAMPLE` were
  paraphrased/abridged. Regenerated ALL prompt-section constants verbatim from
  `memoryTypes.ts` (extracted via node); `test_memory_types_verbatim.py` re-extracts
  and asserts equality.
- *plan-mode* — the rendered text was never delivered: a dict result was
  `str()`-ified into a Python-dict repr the model read. Added module-level
  `map_tool_result_to_tool_result_block_param` support to the wrapper + the TS
  branches (isAgent → "respond with ok", empty-plan, edited-plan label).
- *sandbox* — `bash_permission_rule` parsed `name:*` as a literal wildcard
  (prefix check came after `*`), breaking the excludedCommands prefix syntax;
  reordered to match TS. `is_sandboxing_enabled()` now reads `settings.sandbox.enabled`
  (was test-only → feature was unreachable). Seatbelt `/dev/*` entries fixed to
  `(literal)`; temp dir added to allow-roots; `are_unsandboxed` default → true.
- *agent* — `model` constrained to `['sonnet','opus','haiku']`; `mode` gates
  `auto` on TRANSCRIPT_CLASSIFIER; `subagent_type` is now a free string (TS
  `z.string()`, was a closed enum that rejected user/MCP agents); the team-access
  guard now **raises** (→ `is_error` tool_result) instead of returning success.
- *nit* — dead `get_all_tool_modules` map repointed from the old PlanModeTool
  stubs to the active Enter/ExitPlanModeTool modules.

**Documented scope limits (NOT a faithful 1:1 — honest about it):**
- *sandbox* — the real macOS seatbelt + network isolation lives in the external
  `@anthropic-ai/sandbox-runtime` package, NOT in the recovered source.
  `build_seatbelt_profile` is a hand-rolled **write-restriction approximation**:
  no network isolation, and without the security deny-writes (settings.json,
  `.claude/skills`) that `convertToSandboxRuntimeConfig` builds. OFF by default.
- *plan-mode* — teammate/awaitingLeaderApproval branches, `persistFileSnapshotIfRemote`,
  and the empty-`""`-plan write-through edge are not reproduced (teammate/CCR-only).
- *agent* — `run_in_background` gating, `resolveTeamName`, and the in-process
  teammate spawn guards are not ported (teammate spawning is out of scope).

## System prompt section audit vs 2.1.88 (2026-06-14)

hare now sends a system prompt (bug #8) with sections: Identity, System, Doing-tasks,
Actions, Using-tools, Git-safety, Committing-changes, Output-efficiency,
Tone-and-style, [boundary], Environment, Custom, Session-guidance, **Memory**,
Language, Output-style. The **Memory** section (after session_guidance, matching
2.1.88 dynamicSections order) is now assembled by `load_memory_prompt()` and is
present by default (auto-memory on). Still pending (mostly feature-gated /
ant-only / kairos-gated): **MCP instructions**, **Scratchpad**, **Function-result
clearing**, **Numeric length anchors** (ant), **Token budget**, **Brief**
(kairos). The unconditional **Summarize-tool-results** line is already present.

## Behavioral observations (not bugs)

- Claude Code **auto-continues** on `stop_reason: max_tokens` (issues a follow-up
  request) — a single-response fixture makes the reference hang, so such cases
  need a multi-response fixture. Not collected.

## Reference choice

The locally-installed `claude` (2.1.165) is the working headless reference. The
exact-version `recovered-from-cli-js-map` (2.1.88) does **not** run headless —
its `-p` path hangs in the `await import('src/cli/print.js')` module-load chain
(ruled out: Grove, telemetry, top-level await). Use it for *source* reference
(e.g. the tool registry above), not for recording goldens.
