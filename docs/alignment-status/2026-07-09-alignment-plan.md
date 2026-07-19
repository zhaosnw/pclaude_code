# Hare Alignment Plan（2026-07-19 更新版）

## 目标

终态定义：**一个与正式版 Claude Code 2.1.209 功能对齐、可以在其上持续迭代的 Python code agent。**

这意味着两件事同时成立：

1. **行为对齐可证明。** 每一块已声明对齐的功能，都有 golden E2E case（正式版 oracle 录制的 golden）作为证据。
2. **对齐进度可度量。** parity matrix（5 维度、206 行）每项标注 `aligned` / `implemented-unverified`，能回答"离完成度还有多少"。

## 当前状态（2026-07-19）

### 验证基线

```bash
python -m pytest tests/e2e -q              # 92 passed, 1 xfailed
python -m pytest tests/ -q                 # 2900 passed, 12 skipped, 1 xfailed
make mypy-regression                       # PASS (497)
make alignment-guardrails                  # 16 passed
make dogfood                               # 5/5 passed
make parity-matrix                         # passed (206 rows, 25 aligned)
```

### 对齐覆盖

- **62 个 golden case**，覆盖 chat / cli / hooks / json / limits / mcp / permission / session / stream_json_tools / subagent / tools / compact / behavior
- **64 个已录制 case**（2 个删掉的退化和 2 个录好但被记录分歧，合计 64）
- **1 个 registered divergence**（known_divergence）：`subagent.async_dispatch`（num_turns 差 1）
- **parity matrix 206 行，5 维度**：CLI(117) + tool(44) + hook(27) + settings(8) + behavior(10)；25 项 `aligned`

### Oracle

正式版钉定：`.oracle/claude-2.1.209/`（本机 `/opt/anaconda3/envs/code/bin/claude` 复制钉入，459M，`.gitignore` 覆盖）。
录制入口：`CLAUDE_TS_CLI="$PWD/.oracle/claude-2.1.209/bin/claude.exe" python scripts/record_golden.py <case_id>`

### 已对齐的主要行为轴

| 轴 | 状态 | case 数 |
|---|---|---|
| CLI flags（`-p` 管道、`--resume`/`--continue`、`--permission-mode`、`--mcp-config`、`--settings`、`--max-turns`） | ✅ 1 xfailed | 12 |
| session persistence | ✅ | 4 |
| permission modes × settings（allow / deny / bypass / 优先级 / 重定向） | ✅ | 7 |
| hooks（工具/会话/压缩生命周期全部 10 个 P1 事件） | ✅ | 8 |
| MCP（配置错误、stdio tool call） | ✅ | 2 |
| tools（Bash / Read / Write / Edit / MultiEdit / Glob / Grep / LS / TodoWrite） | ✅ | 17 |
| subagent / Agent（同步路径 `run_in_background=false`） | ✅ | 1 |
| subagent 异步生命周期 + 完成重入 | ⚠️ known_divergence | 1 |
| compact / auto-compact（print 模式不压缩） | ✅ | 1 |
| content-matched fixture harness | ✅ | 1 |

## 对齐原则（不变，补充 2 条）

前 7 条沿用原计划：
1. 唯一 canonical Python 源码树
2. 唯一 golden 主资产目录
3. **Oracle 改为正式版**（不再是 recovered TS/JS）
4. Golden 优先于存量单测
5. 先加观测再改行为
6. 每一步有可复跑验收
7. 不扩大重复资产面

新增：

8. **Golden 必须有真实执行证据。** Scripted fixture 下模型文本是写死的、工具跑没跑 stdout 都一样。一个"通过"的 golden 如果没有证明工具真执行了，就是假通过。必须通过 `check_files`（marker 文件）、`permission_denials` 字段、或引用 API 请求体来提供真实执行证据。

9. **代码审查循环。** 功能对齐不是只补 golden——每完成一轮较大的实现改动（生命周期、权限管线、MCP 路由等），先跑独立验证者审查再合并。具体使用 `scripts/verify/`（见后）提供快速反馈，周期性的多 agent 审查做更深层扫描。

已知的 19 条审查发现（2026-07-16 code review）中，**P0 的 4 条已于 2026-07-17 清偿，P1 的 4 条已于 2026-07-18 清偿，P2 的 3 条已于 2026-07-19 清偿**；P1b 3 条判定当前无需修，见阶段 5。

## 阶段 0 / 1 / 2：地基工作（已完成）

均已落地：垃圾清理、egg-info 移出 git 追踪、路径语义收口、`alignment-guardrails`（16 passed）、`tests/test_hare_tree_cleanup_guard.py`（4 个守卫）。

## 阶段 3：Parity Matrix（已完成）

`scripts/gen_parity_matrix.py` 已投入日常使用。`make parity-matrix` 是 CI 验证的一步。

## 阶段 4：补真差分覆盖（主线，持续进行）

### 已完成

按轴覆盖的单子（比原计划多已完成）：

| 原计划 | 当前 |
|---|---|
| session resume/continue | ✅ 5 case（含 resume_edit_after_read） |
| permission modes × settings | ✅ 7 case |
| hooks（PreToolUse/PostToolUse/Stop/SessionStart/End/PromptSubmit/SubagentStop/Failure/PreCompact/PostCompact） | ✅ 8 case |
| MCP | ✅ 2 case |
| CLI 输出补漏 | ✅（-p stdin 管道、--settings） |
| compact / auto-compact | ✅ 1 case（正式版 print 模式不压缩） |
| subagent / Agent | ✅ 2 case（同步对齐 + 异步 divergence） |

### 完成标准（更新版）

- 每条 P0/P1 轴至少 2 个 golden E2E case：✅ 已满足
- 新 case 不依赖网络、不写真实用户目录：✅
- 差异只通过 `known_divergence` 或修实现处理：✅（当前 1 个 divergence）

## 阶段 4b：剩余覆盖（建议优先级）

当前 181 项 `implemented-unverified` 的分布：

| 维度 | 未验证 | 大项 |
|---|---|---|
| CLI | 115 | 大部分是 P1 flag，code agent 主链路不依赖 |
| tool | 36 | MoveTool、PowerShellTool 等非核心工具 |
| hook | 18 | 17 个 P2 事件（UI/遥测/任务管理、不触发） |
| settings | 5 | env、model 等 |
| behavior | 2 | micro_compact（P2）、token_budget（P2） |

建议优先级：

1. **`chat.whitespace_result` / `chat.empty_text` 已删除，无需再补**。
2. **CLI flag 按需补**—不需要全部对齐，只在发现 bug 或做特征时补。示例高价值：`--allowed-tools`/`--disallowed-tools`（已有探针结果）、`--model`（验证 flag 穿透到模型）、`--output-format stream-json` + 输入侧。
3. **P2 hooks 整体跳过**，直到有人报告差异。
4. **Tool schema 字段对齐**——当前只覆盖了工具名，没覆盖 input schema 的字段级对比。

## 阶段 5：行为清偿

### 已清偿（自 07-09 计划以来）

原计划清单中已完成的修复（每个修复都有对应 golden 证明）：

- session resume 后 Edit 失败（read-state 门）
- Bash 输出重定向从不校验
- `ask` 决策不阻断工具
- MCP 工具从未真正执行（`_McpRuntimeTool.call` 签名不兼容）
- hooks 管线是死代码（PreToolUse / PostToolUse / PostToolUseFailure / Stop 全无调用方）
- lifecycle hooks 从未触发（SessionStart/End、UserPromptSubmit、SubagentStop）
- hook 从未真正阻断工具（getattr 对 dict 返回 allow 默认）
- auto-compact 从未成功运行（类型错误被吞、token 计数字忽略 usage、摘要本地拼字符串而非模型生成）
- Agent 工具别名被 lowercased 导致 Task 找不到工具
- 失败的工具结果不带 `is_error`
- `-p` 值可选支持多进程管道
- 坏 `--mcp-config` 不中止进程
- `--settings` flag 实现
- 跨进程 fixture cursor 共享
- content-matched 录制基础设施

**2026-07-16 代码审查的 4 条 P0（2026-07-17 清偿，subagent-driven-development 两阶段审查通过）：**

- **重入循环的 tool_result 丢失**（`query_engine.py`，commit `830c5a80`）：重入的 `async for` 循环补齐了 `user`/`progress`/`attachment`/`system` 分支，与主循环对齐；`user` 分支刻意不加 `turn_count`（重入不是新用户输入，与 `subagent.async_dispatch` 的 known_divergence 一致）。回归测试 `tests/e2e/test_subagent_async_reentry.py::test_reentry_tool_use_gets_a_matching_tool_result` 证明修复前会产生悬空 tool_use。
- **重入的 max_turns 膨胀**（`query_engine.py`，commit `830c5a80` + `462f573c`）：新增 turn 消耗跟踪，重入时传递递减后的剩余预算而非原始 max_turns。首版实现按 yielded `assistant` 消息计数，被代码审查指出对多 content block 的单个 turn 会重复计数（`hare/tests/test_hare_api_client_streaming.py` 早已验证流式客户端按 block 而非按 turn 产出 `AssistantMessage`）；修正为改用 `query()` 的 `on_transition`/`Continue(reason="next_turn")` 信号计数真实 turn 边界。同一轮审查还发现重入循环缺少主循环的 `max_budget_usd` 熔断检查，已一并补齐。回归测试：`test_max_turns_is_not_inflated_across_multiple_reentries`、`test_multi_block_turn_counts_as_one_turn_not_one_per_block`。
- **后台子代理超时泄漏**（`query_engine.py`，commit `830c5a80`）：drain while 循环不再对 `wait_for_next_completion()` 超时返回的 `None` 一律 `break`；先查 `async_agent_tasks.has_pending()`，任务仍在跑就继续轮询，只有真正无 pending 时才退出。回归测试 `test_completion_survives_a_mid_poll_timeout`。
- **后台 agent_id 不一致**（`agent_tool.py`，commit `405250e6`）：`run_in_background` 分支改为只生成一个 `subagent_id`，同时用作 `child_engine` 的 `AgentId`（驱动 `SubagentStop` hook）和异步完成/`<task-notification>` 的 `agent_id`/`tool_use_id`。回归测试 `test_subagent_stop_hook_agent_id_matches_task_notification_id` 独立比对 hook 捕获值、launched 消息里的 `agentId:`、notification 里的 `<task-id>` 三处取值。

四条修复均跑过 `python -m pytest tests/e2e -q && python -m pytest tests/ -q && make mypy-regression && make alignment-guardrails && make dogfood`，`subagent.async_dispatch` 的 known_divergence（仅 num_turns，hare 2 vs reference 1）保持不变。

**2026-07-16 代码审查的 4 条 P1（2026-07-18 清偿，subagent-driven-development 两阶段审查通过）：**

- **`AsyncAgent._registry` 只增不删**（`async_agent_tasks.py`，commit `677eb5f3` + `90e8f910`）：新增 `_prune_done_tasks()`，在 `has_pending()`/`wait_for_next_completion()` 读取时把 `done()` 的 task 从 `_registry.tasks` 里清掉，而不是永久持有。同一提交顺带修了 `agent_tool.py`（`_run_background()`）里 `except Exception: pass` 不捕获 `asyncio.CancelledError`（`BaseException` 子类）的问题，改为显式 `except asyncio.CancelledError: raise`（代码审查指出这在本仓库 Python 3.11+ 上其实是无害的防御性写法，原 bug 描述的"被 Exception 吞掉"机制本身不准确——`CancelledError` 从来都不是 `Exception` 子类，但取消后 registry 不清理的问题是真实的，已随 pruning 一并解决）。回归测试最初误放在不受 CI 门禁收集的 `hare/tests/`（legacy 镜像层），已在 `90e8f910` 中迁移到 canonical `tests/test_async_agent_tasks.py` 和 `tests/e2e/test_subagent_async_reentry.py`（`tests/e2e` 92→92 保持，`tests/` 2883→2887）。
- **SessionEnd hooks 在 finally 块阻塞退出**（`main.py` + `hooks/__init__.py` + `exec_hook.py`，commit `6aad1afb` + `6d4d05f0`）：`execute_session_end_hooks()` 新增可选 `timeout_sec` 参数，退出路径传 5 秒（`SESSION_END_EXIT_HOOK_TIMEOUT_MS`）取代原本每个 hook 最多 10 分钟的默认值；不传时其余调用方行为不变。同时修了 `exec_hook.py` 超时/取消路径从未真正杀掉子进程的问题（`asyncio.wait_for` 超时只停止等待，不终止子进程）——新增 `_kill_orphaned_process()`，`kill()` 后 `wait()` 避免僵尸进程，覆盖 `TimeoutError` 和从外层传入的 `CancelledError` 两条路径。代码审查后追加 `6d4d05f0` 把 `kill()` 的异常保护从只挡 `ProcessLookupError` 扩到挡所有 `OSError`，避免小概率场景下把待传播的 `CancelledError` 意外吞掉。`tests/` 2887→2893（新增 6 个测试，均在 canonical `tests/`）。
- **`resolve_hook_permission_decision` 不在 except 保护内**（`tool_execution.py`，commit `26ede65f`）：`resolve_hook_permission_decision(...)` 调用及其依赖的结果处理包进 `try/except Exception/else`，与紧邻上方 `run_pre_tool_use_hooks` 的既有保护模式一致——异常时把 `hook_permission` 重置为 `None`，走正常的 rule-based/`can_use_tool` 权限流程（fail-open，不是 fail-closed，也不是静默放行：仍然过一遍真实的权限检查）。成功路径的 deny/ask/allow/passthrough 逻辑原样保留在 `else` 里，未做语义改动。`tests/` 2893→2894。

四条修复同样均跑过完整验证门禁（e2e / 全量单测 / mypy-regression / alignment-guardrails / dogfood），无回归。

**2026-07-19 合入前全量复审（`8db2fefd..3c36f94d`）：通过，无新正确性缺陷。**

对上述 P0+P1 两轮修复的完整 diff（10 个 commit、13 个文件、+1571/-112）做了一次整体复审（多 agent workflow 的 finder 全部撞上 600s 看门狗超时 stall，改为人工逐文件审查，随后独立验证复核）。独立验证确认的关键点：

- `_TurnConsumptionCounter` 的 `next_turn_transitions + 1` 公式在所有场景成立：`query/core.py` 的 max_turns 检测（`next_turn_count > params.max_turns` 后直接 `return`）发生在 `Continue(reason="next_turn")` transition 之前，且该 transition 是全代码库唯一触发点，所以超限场景同样是 N-1 次 transition + 1 = N；零轮错误场景无 assistant 消息，计 0。
- `_prune_done_tasks()` 的 check→rebuild 是纯同步代码（无 await 点），单线程 asyncio 下无竞态。
- `exec_hook.py` 的 `kill()` + `await wait()` 正确避免僵尸进程；`returncode is not None` 短路已被 reap 的情况。
- `tool_execution.py` 的 `try/except/else` 对成功路径是纯重构：`else` 分支原样保留 deny/ask 阻断和 allow 传递，`resolved_input` 只在无异常时赋回 `tool_input`。
- 4 个新回归测试文件（10 个测试）+ 全量 `tests/`（2802 + 92 e2e = 2894）复跑通过。

两个不影响正确性的观察（已通过 ReportFindings 上报，不阻塞 push）：

1. 重入循环的 `progress` 消息与主循环一致地不调 `_persist_message()`——两边行为一致，非新 bug；若将来要求 session 重载后保留 progress，两处需同步改（见「暂停点」的双循环同步条目）。
2. `_budget_exhausted()` 的 `remaining_turns <= 0` 守卫隐式保护了 `query()` 对 `max_turns=0` 的 falsy 语义（见「暂停点」新增条目）。

**2026-07-19 剩余 3 条 P2 清偿：**

- **cursor+consumed 文件 TOCTOU 竞态**（`hare/testing/fake_model.py`）：`fixture_call_model` 的 docstring 早已言明 `cursor_path` 是"跨进程共享回放位置"——一个后台子代理自己的 `python -m hare` 调用和它的父进程都指向同一个 cursor/consumed 文件——但读取→决策→写回全程没有任何跨进程同步。新增 `_cross_process_lock()`（POSIX `fcntl.flock`，非 POSIX 平台 best-effort 退化为不加锁），把 `_claim_next_index()`（scripted 游标）和 `_select_by_content()`（content-matched 的 consumed 集合）的读-决策-写临界区整体纳入锁保护。回归测试 `tests/test_fake_model_cursor_lock.py` 用真实 OS 子进程（`multiprocessing`，`spawn` 而非 `fork`——pytest 进程本身是多线程的，`fork()` 多线程父进程有文档记载的子进程死锁风险）并发抢同一个 cursor/consumed 文件，断言 8 个进程从 8 响应 fixture 里各拿到互不重复的响应、`once` 响应在 8 个并发请求下只被服务一次。
- **tool_execution 多处 import 在 `except: pass` 内**（`hare/services/tools/tool_execution.py`）：4 处 hook 调用点的 `from hare.services.tools.tool_hooks import ...` 原本和 hook 运行时调用共享同一个 `except Exception`，一次重构把某个 hook 函数改名/删除产生的 `ImportError` 会和"某个第三方 hook 运行时抛错"完全无法区分，静默地把该 hook 管线关掉。新增 `except ImportError` 分支（在通用 `except Exception` 之前），`logger.error(..., exc_info=True)` 记录一条独立可辨识的日志，同时保持"broken hook 不得杀死当前 turn"的既有行为不变（顺带发现并修了 `resolve_hook_permission_decision` 那处：P1 commit `26ede65f` 把 import 留在 try 外面，导致这一个点上 ImportError 仍会直接崩掉整个 turn——现在和其余三处一致地纳入 try 内 + 专属 `except ImportError`）。回归测试 `tests/test_tool_execution_hook_import_guard.py` 通过临时删除 `tool_hooks` 模块上的目标函数模拟 ImportError，断言 turn 存活、工具正常跑完，且 caplog 里出现可辨识的错误日志。
- **mock server 5xx = SDK 无限重试**（`scripts/mock_anthropic_server.py`）：官方 Anthropic SDK 的 `shouldRetry()`（`recovered-from-cli-js-map/node_modules/@anthropic-ai/sdk/client.js`）对 408/409/429/>=500 一律重试（默认 `maxRetries=2`，指数退避）。"没有 fixture 响应匹配这个请求"和"fixture 已耗尽"都是 fixture 作者的配置错误，不是瞬时故障，之前用 `send_error(500, ...)` 上报会让 SDK 先烧掉重试预算再失败，调用方看到的是一个不透明的超时/网络错误而非直观的错误信息。改为 `send_error(400, ...)`（不在 SDK 重试白名单内）。回归测试 `tests/test_mock_anthropic_server.py` 新增两个用例，分别验证"无匹配"和"fixture 耗尽"两条路径返回的状态码都不是 SDK 会重试的那几个。

三条改动跑过完整验证门禁（`tests/` 2894→2900，新增 6 个回归测试；`tests/e2e` 92 passed 不变；`make mypy-regression` 497/497 持平；`make alignment-guardrails` 16 passed；`make parity-matrix --check` 通过；`python scripts/detect_stubs.py` 未超限；`make dogfood` 5/5），无回归。

### 待修（来自 2026-07-16 代码审查，按优先级）

P0、P1、P2 共 11 条已全部清偿（P2 三条于 2026-07-19 清偿，见上「已清偿」）。

**P1b（可能无需修）**

- **`build_task_notification` 不做 XML 转义**——无实际影响（差分不比 notification 原文）。
- **`_align_result_schema` 没 setdefault `permission_denials`**——当前所有路径都带这个键。
- **`normalize_result` 的 sandbox path 擦除可能掩盖输出差异**——但有 check_files 兜底。

### 推荐执行顺序

1. ~~修 P0 的 4 条（先修 tool_result 泄漏确保 transcript 完整）~~ ✅ 已于 2026-07-17 清偿，见上「已清偿」。
2. ~~修 P1 的 4 条~~ ✅ 已于 2026-07-18 清偿，见上「已清偿」。
3. ~~修 P2 的 3 条~~ ✅ 已于 2026-07-19 清偿，见上「已清偿」。
4. 每个修复跑 `python -m pytest tests/e2e -q && make mypy-regression && make alignment-guardrails`。
5. 间隔做 dogfood 验证。
6. **2026-07-16 审查的 19 条发现已全部处理完（P0/P1/P2 共 11 条清偿 + P1b 3 条判定无需修）。下一步转向阶段 4b 按需补覆盖，见下方主线 B。**

## 阶段 6：Dogfood（已完成并接入 CI）

`scripts/dogfood.py`（5 场景），`make dogfood` 已接入（失败返回非零）。

已知偶发问题：MCP 场景挂起过一次（120s 超时，非缓慢），连续 4 次通过后记为待观察。

## 阶段 7：mypy（只保门）

当前基线 `497`，`make mypy-regression` 持续通过。主动清偿排在功能对齐之后。

## 阶段 8：收口检查

### 每次推送前执行

```bash
git status --short --branch
make alignment-guardrails
python -m pytest tests/e2e -q
python scripts/gen_parity_matrix.py --check
make mypy-regression
python scripts/detect_stubs.py
```

## 建议执行顺序（续）

当前状态：**2026-07-16 审查的 19 条发现（P0 4 条 + P1 4 条 + P2 3 条 + P1b 3 条判定无需修 + 已有清偿事项）已全部处理完**（P0 2026-07-17、P1 2026-07-18、P2 2026-07-19 分批完成）。后续工作分为两条线：

**主线 A：审查发现的 P0/P1/P2 已全部清偿**
- ~~tool_result 泄漏 → max_turns 膨胀 → agent_id 不一致 → 超时泄漏~~ ✅ 已清偿（commit `830c5a80`、`462f573c`、`405250e6`）
- ~~`AsyncAgent._registry` 内存泄漏 → `CancelledError` 不被后台 drainer 捕获 → `SessionEnd` hooks 阻塞退出 → `resolve_hook_permission_decision` 无异常保护~~ ✅ 已清偿（commit `677eb5f3`、`90e8f910`、`6aad1afb`、`6d4d05f0`、`26ede65f`）
- ~~cursor+consumed TOCTOU、tool_execution 的 `except: pass` import、mock server 5xx 无限重试~~ ✅ 已清偿（2026-07-19，见阶段 5「已清偿」）

**主线 B：按需补覆盖**
- 不在 CLI flag 上消耗大量时间。按需补：发现 bug 时补 case + 修；受报告驱动的 flag 做 golden。
- 高价值：`--output-format stream-json` 输入侧、`--input-format stream-json`。

## 暂停点（新增）

在已有暂停点基础上增加（专门针对本轮审查发现）：

- ~~重入循环新增 `user`/`progress`/`attachment` 分支时必须加 `turn_count += 1` 决策~~ ✅ 已实现（`user` 分支不加，与主循环对齐但保留刻意差异）；实现中额外发现 turn 计数不能按 yielded `assistant` 消息数（流式客户端按 content block 产出，一个 turn 可能产出多条），已改用 `on_transition`/`next_turn` 信号计数，见「已清偿」。
- 后台任务注册表若改为跨引擎共享数据结构，必须重新评估加锁或改用 per-engine 实例。
- ~~mock server 的 content-matched `once` 文件若加锁逻辑，不得引入死锁（锁内无网络/IO wait）~~ 2026-07-19 已实现（`hare/testing/fake_model.py` 的 `_cross_process_lock`）：锁的临界区只有本地文件 read/write，无网络/IO wait，不存在死锁风险；用 `multiprocessing`（`spawn`）而非 `fork` 起并发测试进程，因为 pytest 自身是多线程进程，`fork()` 一个多线程父进程有文档记载的子进程死锁风险——这条本身也成为以后写并发回归测试时的默认选择。
- 在一次修复中同时修 3 条以上 P0 发现时，e2e 全量 + dogfood 是必须的。
- 新增：重入循环与主循环现存在 ~90 行结构相近的消息分发逻辑（代码审查已指出重复导致过一次 drift——`max_budget_usd` 检查最初只补在主循环）。未做抽取（评估为有风险、需要独立测试覆盖），留作后续任务；下一次任一循环改动都要检查另一循环是否也要同步改。已知的刻意一致点：两循环的 `progress` 分支都不做 `_persist_message()`。
- 新增（2026-07-19 复审）：`query/core.py` 的 `params.max_turns and next_turn_count > params.max_turns` 把 `max_turns=0` 当作"无限制"（0 是 falsy）。`query_engine.py` 目前靠 `_budget_exhausted()` 的 `remaining_turns <= 0` 守卫保证不会把 0 作为 `max_turns` 传进重入的 `query()`。这个耦合是隐式的——改动任一侧（守卫条件、或 query() 的 max_turns 语义）前先确认另一侧，最好顺手在守卫处加注释点明。

## 下一步立刻可开工的任务

2026-07-16 审查的 19 条发现中 P0（4条，commit `830c5a80`/`462f573c`/`405250e6`）、P1（4条，commit `677eb5f3`/`90e8f910`/`6aad1afb`/`6d4d05f0`/`26ede65f`）、P2（3条，2026-07-19，见「已清偿」）均已清偿，P1b 3 条判定当前无需修。2026-07-19 对 P0+P1 完整 diff（`8db2fefd..3c36f94d`）的合入前复审也已通过，无新正确性缺陷。

当前没有已知的阻塞性问题。下一步工作转向阶段 4b 的「按需补覆盖」（不主动对齐全部 CLI flag，发现 bug 或做特征时再补 golden case）——参见「建议执行顺序（续）」的主线 B。若之后再有代码审查发现新的问题，按同样的模式处理：每条修复配一个可复跑的回归测试（放在 canonical `tests/`，不要放 `hare/tests/`），过 spec-reviewer + code-quality-reviewer 两阶段审查，再跑 `python -m pytest tests/e2e -q && python -m pytest tests/ -q && make mypy-regression && make alignment-guardrails`，多条一起改时补 `make dogfood`。
