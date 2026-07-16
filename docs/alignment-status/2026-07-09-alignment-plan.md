# Hare Alignment Plan（2026-07-16 更新版）

## 目标

终态定义：**一个与正式版 Claude Code 2.1.209 功能对齐、可以在其上持续迭代的 Python code agent。**

这意味着两件事同时成立：

1. **行为对齐可证明。** 每一块已声明对齐的功能，都有 golden E2E case（正式版 oracle 录制的 golden）作为证据。
2. **对齐进度可度量。** parity matrix（5 维度、206 行）每项标注 `aligned` / `implemented-unverified`，能回答"离完成度还有多少"。

## 当前状态（2026-07-16）

### 验证基线

```bash
python -m pytest tests/e2e -q              # 86 passed, 1 xfailed
python -m pytest tests/ -q                 # 2878 passed, 12 skipped
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

已知的 19 条审查发现（2026-07-16 code review）中，**前 4 条应在下一轮优先修完**，见阶段 5。

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

### 待修（来自 2026-07-16 代码审查，按优先级）

**P0：影响行为正确性**

1. **重入循环的 tool_result 丢失**（`query_engine.py:463`）：重入的 async for 循环只处理 assistant 和 stream_event，不处理 user（tool_result 和 progress/attachment）。当重入后的模型响应一个 tool_use 时，工具执行的 tool_result 不落 `_mutable_messages` 也不持久化——transcript 里有悬空 tool_use。**这是当前最该修的 bug。**
2. **后台子代理超时泄漏**（`async_agent_tasks.py:67`）：`wait_for_next_completion` 30 秒超时 return None → while 循环 break。task 完成后 `record_completion` 写入但无人 drain，子代理结果永久丢失。
3. **重入的 max_turns 膨胀**（`query_engine.py:460`）：每次重入复制原始 max_turns，不是减量传递。N 次重入 → 总 turn 预算膨胀到 O(M·(N+1))。
4. **后台 agent_id 不一致**（`agent_tool.py:237 vs 1494`）：子代理引擎的 agent_id 和异步通知的 agent_id 是两个不同的 uuid4()。SubagentStop hook 和 task-notification 各指一个不匹配的 ID。

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

1. 修 P0 的 4 条（先修 tool_result 泄漏确保 transcript 完整）。
2. 每个修复跑 `python -m pytest tests/e2e -q && make mypy-regression && make alignment-guardrails`。
3. P1 里优先修 `async_agent_tasks.reset()` 和 `CancelledError`。
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

当前状态：**已完成"快速覆盖"阶段**。后续工作分为三条线：

**主线 A：修复审查发现的 P0 问题（当前优先）**
- tool_result 泄漏 → max_turns 膨胀 → agent_id 不一致 → 超时泄漏
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

- 重入循环新增 `user`/`progress`/`attachment` 分支时必须加 `turn_count += 1` 决策——与主循环对齐，但只在**特定条件下**才应递增。
- 后台任务注册表若改为跨引擎共享数据结构，必须重新评估加锁或改用 per-engine 实例。
- mock server 的 content-matched `once` 文件若加锁逻辑，不得引入死锁（锁内无网络/IO wait）。
- 在一次修复中同时修 3 条以上 P0 发现时，e2e 全量 + dogfood 是必须的。

## 下一步立刻可开工的任务

1. **修重入 tool_result 泄漏**（P0·1）：在 `query_engine.py` 重入循环补齐 `user`、`progress`、`attachment` 分支——与主循环对齐，注意 `user` 分支不加 `turn_count`（重入不是新用户输入）。
2. **修 max_turns 膨胀**（P0·3）：重入的 `QueryParams` 传 `remaining_max_turns` 而非原始值。
3. **同步 agent_id**（P0·4）：后台启动用同一个 AgentId，不再额外 `str(uuid4())`。
4. **修超时泄漏**（P0·2）：`wait_for_next_completion` 超时后检查一次 `_registry.completions` 再决定 break 还是继续等。
