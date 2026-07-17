# Hare Alignment Plan（2026-07-17 更新版）

## 目标

终态定义：**一个与正式版 Claude Code 2.1.209 功能对齐、可以在其上持续迭代的 Python code agent。**

这意味着两件事同时成立：

1. **行为对齐可证明。** 每一块已声明对齐的功能，都有 golden E2E case（正式版 oracle 录制的 golden）作为证据。
2. **对齐进度可度量。** parity matrix（5 维度、206 行）每项标注 `aligned` / `implemented-unverified`，能回答"离完成度还有多少"。

## 当前状态（2026-07-17）

### 验证基线

```bash
python -m pytest tests/e2e -q              # 91 passed, 1 xfailed
python -m pytest tests/ -q                 # 2883 passed, 12 skipped, 1 xfailed
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

已知的 19 条审查发现（2026-07-16 code review）中，**前 4 条 P0 已于 2026-07-17 清偿**；下一轮优先修 P1 的 4 条，见阶段 5。

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

### 待修（来自 2026-07-16 代码审查，按优先级）

**P1：影响持久化或健壮性**

5. **`AsyncAgent._registry` 只增不删**——长会话轻微内存泄漏。
6. **`CancelledError` 不被后台 drainer 捕获**——Ctrl+C 时 record_completion 不触发。
7. **SessionEnd hooks 在 finally 块阻塞退出**——command hook 挂起可能锁住进程。
8. **`resolve_hook_permission_decision` 不在 except 保护内**——tool.check_permissions 抛异常会崩整个 turn。

**P2：边界场景/测试设施**

9. **cursor+consumed 文件 TOCTOU 竞态**——内容匹配的 once 语义在 IPC 场景不可靠。
10. **tool_execution 多处 import 在 `except: pass` 内**——重构吞 ImportError 会让 hooks 静默失效。
11. **mock server 5xx = SDK 无限重试**——内容匹配无响应时 fixture 作者收到超时而非直观错误。

**P1b（可能无需修）**

- **`build_task_notification` 不做 XML 转义**——无实际影响（差分不比 notification 原文）。
- **`_align_result_schema` 没 setdefault `permission_denials`**——当前所有路径都带这个键。
- **`normalize_result` 的 sandbox path 擦除可能掩盖输出差异**——但有 check_files 兜底。

### 推荐执行顺序

1. ~~修 P0 的 4 条（先修 tool_result 泄漏确保 transcript 完整）~~ ✅ 已于 2026-07-17 清偿，见上「已清偿」。
2. 每个修复跑 `python -m pytest tests/e2e -q && make mypy-regression && make alignment-guardrails`。
3. **P1 里优先修 `AsyncAgent._registry` 只增不删（长会话内存泄漏）和 `CancelledError` 不被后台 drainer 捕获——当前优先级。**
4. 间隔做 dogfood 验证。
5. P2 为"已知但不阻塞"。

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

当前状态：**已完成"快速覆盖"阶段，2026-07-16 审查的 4 条 P0 已于 2026-07-17 全部清偿**。后续工作分为三条线：

**主线 A：修复审查发现的 P1 问题（当前优先）**
- ~~tool_result 泄漏 → max_turns 膨胀 → agent_id 不一致 → 超时泄漏~~ ✅ 已清偿（commit `830c5a80`、`462f573c`、`405250e6`）
- 下一批：`AsyncAgent._registry` 内存泄漏 → `CancelledError` 不被后台 drainer 捕获 → `SessionEnd` hooks 阻塞退出 → `resolve_hook_permission_decision` 无异常保护
- 每修一个跑一遍 dogfood → e2e → mypy 门

**主线 B：按需补覆盖**
- 不在 CLI flag 上消耗大量时间。按需补：发现 bug 时补 case + 修；受报告驱动的 flag 做 golden。
- 高价值：`--output-format stream-json` 输入侧、`--input-format stream-json`。

**支线：基础设施增强**
- 后台任务注册表 task 清理（`_registry.tasks` prune completed）
- mock server 无匹配时给更快反馈（而非 SDK 重试 2 分钟后超时）
- Content-matched harness 的并发安全性提升

## 暂停点（新增）

在已有暂停点基础上增加（专门针对本轮审查发现）：

- ~~重入循环新增 `user`/`progress`/`attachment` 分支时必须加 `turn_count += 1` 决策~~ ✅ 已实现（`user` 分支不加，与主循环对齐但保留刻意差异）；实现中额外发现 turn 计数不能按 yielded `assistant` 消息数（流式客户端按 content block 产出，一个 turn 可能产出多条），已改用 `on_transition`/`next_turn` 信号计数，见「已清偿」。
- 后台任务注册表若改为跨引擎共享数据结构，必须重新评估加锁或改用 per-engine 实例。
- mock server 的 content-matched `once` 文件若加锁逻辑，不得引入死锁（锁内无网络/IO wait）。
- 在一次修复中同时修 3 条以上 P0 发现时，e2e 全量 + dogfood 是必须的。
- 新增：重入循环与主循环现存在 ~90 行结构相近的消息分发逻辑（代码审查已指出重复导致过一次 drift——`max_budget_usd` 检查最初只补在主循环）。未做抽取（评估为有风险、需要独立测试覆盖），留作后续任务；下一次任一循环改动都要检查另一循环是否也要同步改。

## 下一步立刻可开工的任务

2026-07-16 审查的 4 条 P0 已全部清偿（见「已清偿」，commit `830c5a80` / `462f573c` / `405250e6`）。下一批（P1，按`阶段 5`列出的优先级）：

1. **`AsyncAgent._registry` 只增不删**（P1·5）：长会话轻微内存泄漏，需要 prune 已完成/已消费的任务和 completion。
2. **`CancelledError` 不被后台 drainer 捕获**（P1·6）：Ctrl+C 时 `record_completion` 不触发，需要在后台任务的取消路径上补处理。
3. **`SessionEnd` hooks 在 finally 块阻塞退出**（P1·7）：command hook 挂起可能锁住进程，需要超时/非阻塞化。
4. **`resolve_hook_permission_decision` 不在 except 保护内**（P1·8）：`tool.check_permissions` 抛异常会崩整个 turn，需要异常边界。

每修一条按「已清偿」条目的模式跑 `python -m pytest tests/e2e -q && python -m pytest tests/ -q && make mypy-regression && make alignment-guardrails`，多条一起改时补 `make dogfood`。
