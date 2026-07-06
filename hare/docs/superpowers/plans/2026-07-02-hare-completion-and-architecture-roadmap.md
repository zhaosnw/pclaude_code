# hare 完善计划与架构演进路线图

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **注意分层**:本文档是**总路线图**(roadmap of plans)。只有 Phase 0 是可以直接逐步执行的完整计划;Phase 1–4 的每个任务组在开工时应各自用 writing-plans 技能展开成独立的可执行计划(存放于本目录,命名 `YYYY-MM-DD-<任务名>.md`)。

**Goal:** 把 hare 从"骨架完整、真差分覆盖薄、工程卫生欠账"的状态,推进到"可判定验收的 2.1.88 行为等价 + 有结构性优势的 Python 架构"。

**Architecture:** 验证先行(先把 oracle 做厚,再补实现深度);在忠实移植验收之后,分四项结构性创新演进:契约即数据、事件溯源会话内核、纯内核+端口(确定性仿真)、类型棘轮。

**Tech Stack:** Python 3.11+ 纯 stdlib 内核(现状 `dependencies = []`,视为资产)、pytest/Hypothesis、官方 `claude` CLI(oracle 录制)、mypy、GitHub Actions。

**事实基线(2026-07-02 评审实测,详见根目录 `REVIEW_2026-07-02.md`):**
unit 1144 / alignment 1585 / e2e 64 全绿;真差分仅 41 个 golden case;mypy 497 errors(基线 210);463 个 `hare/hare/*.py` 未入 git;hare/hare 208,642 行 vs TS 非 UI 386,577 行;近 4 个 commit 每轮对抗审计仍挖出 confirmed bug。

---

## 总原则(先拍板,后动手)

1. **验证先行**:实现深度补齐必须跟着差分轴走——先有能暴露 bug 的 oracle,再补代码。反过来做会重复"写完了以为对了"的循环。
2. **每阶段一个可判定的验收门**:门是命令 + 期望输出,不是形容词。
3. **移植期不做 Python 化重构**:目录结构、命名、控制流保持镜像 TS,直到 Phase 4 验收过门。架构创新(Part B)全部设计为**不破坏镜像结构的增量层**。
4. **agent 自报不可信**(项目既有教训,两次验证):所有"已对齐/已完成"声明必须落成可重跑的测试或快照。

---

# Part A · 完善计划(五阶段)

## 阶段总览

| 阶段 | 主题 | 前置 | 预估 | 验收门(一句话) |
|---|---|---|---|---|
| Phase 0 | 工程卫生止血 | 无 | 1 天 | `git status --porcelain` 干净;mypy 门恢复有效 |
| Phase 1 | 验证收敛(差分轴扩展) | P0 | 2-3 周 | 6 条新轴各 ≥3 golden case 绿;nightly 差分 CI 跑通 |
| Phase 2 | 深度补齐(靠 oracle 拉动) | P1 | 4-6 周 | 连续 2 轮对抗审计零新 confirmed bug |
| Phase 3 | 交互层决策与实施 | P1(可与 P2 并行) | 拍板半天 + 实施 2-4 周(若做) | SCOPE.md 定稿;若做 TUI 则冒烟脚本绿 |
| Phase 4 | 发布验收 | P2 | 1 周 | `ALIGNMENT_DEFINITION.md` 的 release_ready 公式全项通过 |

---

## Phase 0 · 工程卫生止血(立即执行,完整步骤)

**Files:**
- Modify: `<repo>/.gitignore`
- Modify: `hare/Makefile`(mypy 基线数字)
- Create: `<repo>/docs/archive/`(归档目录)
- Modify: `<repo>/README.md`(指向现行文档)

### Task 0.1: 提交目录重组,救回 463 个未跟踪源文件

- [ ] **Step 1: 先加 .gitignore,防止垃圾入库**

在 `<repo>/.gitignore` 追加(先读现有内容避免重复):

```gitignore
# build/test artifacts
__pycache__/
*.pyc
.coverage
coverage.xml
.mypy_cache/
.pytest_cache/
.ruff_cache/
.hypothesis/
*.egg-info/
htmlcov/

# embedded node projects
hare/frontend/node_modules/
recovered-from-cli-js-map/node_modules/
```

- [ ] **Step 2: 核对将要入库的内容**

```bash
cd <repo> && git add -A --dry-run | grep -v __pycache__ | head -50
git status --porcelain | awk '{print $1}' | sort | uniq -c
```

期望:`??` 项大幅减少且不含 node_modules/缓存;`D` 1315 项(旧扁平路径)全部变为 staged 删除。

- [ ] **Step 3: 整体提交重组**

```bash
git add -A
git commit -m "chore: commit the hare/ nested-project restructure in full

- remove stale flat hare/*.py copies superseded by hare/hare/
- track 463 previously-untracked source files, 77 tests, 20 scripts
- ignore build/test artifacts and embedded node_modules"
```

- [ ] **Step 4: 验证**

```bash
git status --porcelain | wc -l   # 期望: 0
git ls-files 'hare/hare/*.py' | wc -l   # 期望: ≈1376(全部源文件已跟踪)
```

### Task 0.2: 恢复 mypy 门的有效性

- [ ] **Step 1: 取当前真实错误数**

```bash
cd <repo>/hare && python -m mypy hare/ --ignore-missing-imports --show-error-codes --warn-unreachable 2>&1 | tail -1
```

2026-07-02 实测为 `Found 497 errors in 174 files`。

- [ ] **Step 2: 把 Makefile 基线重置为实测值并注明日期**

`hare/Makefile` 中:

```makefile
mypy-regression: ## Check mypy error count against baseline (497 @ 2026-07-02, ratchet down only)
	python scripts/check_mypy_regression.py --baseline 497
```

- [ ] **Step 3: 验证门恢复**

```bash
make -C hare mypy-regression   # 期望: PASS(=497);任何新增错误立刻 FAIL
```

- [ ] **Step 4: Commit**

```bash
git add hare/Makefile && git commit -m "chore: reset mypy baseline to measured 497 (ratchet down only)"
```

### Task 0.3: 归档过期报告 + 文档指路

- [ ] **Step 1: 归档 5 月的五份根目录报告**

```bash
mkdir -p docs/archive
git mv ALIGNMENT_CHECKLIST.md ALIGNMENT_EVALUATION_AND_CI_PLAN.md \
       ALIGNMENT_EXECUTABLE_PLAN.md COMPREHENSIVE_ALIGNMENT_REPORT.md \
       FINAL_ALIGNMENT_REPORT.md GOAL-alignment-BCD.md alignment-progress-bcd.md \
       docs/archive/
```

- [ ] **Step 2: README 增加"现行文档"一节**

在 `<repo>/README.md` 顶部加:

```markdown
## 现行文档(其余为历史归档,见 docs/archive/)
- 评审现状: `REVIEW_2026-07-02.md`
- 路线图: `hare/docs/superpowers/plans/2026-07-02-hare-completion-and-architecture-roadmap.md`
- E2E 差分手册: `hare/docs/e2e-testing.md`
- bug/gap 台账: `hare/docs/alignment-findings.md`
```

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "docs: archive stale May reports; point README at living docs"
```

**Phase 0 验收门:** `git status --porcelain` 为空;`make -C hare mypy-regression` 通过且基线=实测;README 指路生效。

---

## Phase 1 · 验证收敛:六条差分轴 + 差分 CI 化

> 核心论点:现在最大的正确性风险不是"测试不够多"(2793 个),而是"以 TS 原版为 oracle 的测试太少"(41 个)。本阶段只做一件事:**把 oracle 做厚**。

### 1.1 差分轴矩阵(每轴开工时用 writing-plans 展开)

沿用既有闭环:`alignment/cases/<g>/<n>/case.json` → `scripts/record_golden.py` 录 golden → `tests/e2e/test_e2e_cases.py` 比对。fixture 格式与 runner 均已就绪,新轴主要是**写 case + 录 golden + 修暴露的 bug**。

| 轴 | 优先 | case 建议(≥3/轴) | golden 断言重点 | 已知风险区 |
|---|---|---|---|---|
| **resume/continue** | P0 | `resume/basic_continue`(两次 `-p` + `--continue`);`resume/by_session_id`(`--resume <id>`);`resume/after_tool_use`(首轮含 Bash/Write,续轮引用其结果);`resume/missing_session`(错误路径) | 第二轮请求的 messages 里包含首轮完整对话;JSONL 链 parent 关系;错误码/错误文案 | 刚修完 0e8c226/370a927 的区域,回归风险最高 |
| **subagent(Agent 工具)** | P0 | `subagent/basic_spawn`(fixture 驱动父子两层);`subagent/tool_restriction`(子代理调用被禁工具);`subagent/result_propagation`(子代理 final text 回父) | 子代理请求的 system prompt(专用)与 tools 列表(受限);父侧 tool_result 内容 | 已有 xfail 起点(42cbda2);87937fa 刚修 subagent_type 解析 |
| **hooks** | P0 | `hooks/pretooluse_block`(hook 拒绝工具);`hooks/posttooluse_output`(hook 追加上下文);`hooks/stop_block`(Stop hook 阻止收尾,已有单测,补 CLI 级);`hooks/settings_matrix`(settings.json 配置的 hook 生效) | hook 进程真实执行(以副作用文件为证)、拒绝文案、退出码 | hooks 是纯请求侧+副作用,输出 fixture 完全看不到 |
| **permission 矩阵** | P1 | `perm/mode_plan`,`perm/mode_bypass`,`perm/allowlist_settings`(settings allow 规则命中),`perm/deny_rule_wildcard`(`Bash(npm:*)` 类规则) | 工具是否执行(文件副作用)、denial 文案、`permission_denials` 字段 | `:*` 前缀解析出过 bug(0a39e71);已有 2 个 deny case,缺 allow/矩阵 |
| **MCP** | P1 | `mcp/stdio_list_tools`(mock stdio server 列工具进 tools);`mcp/tool_call`(模型调 MCP 工具);`mcp/resources`(list/read resources,09cc16d 刚补) | 请求侧 tools 列表含 `mcp__` 前缀工具;tool_result 内容 | 需要一个测试用 stdio MCP server(几十行 Python 即可,放 `tests/fixtures/mcp_echo_server.py`) |
| **compact 边界** | P1 | `compact/auto_trigger`(fixture 报高 input_tokens 触发 auto-compact);`compact/boundary_persist`(compact 后 JSONL 有 boundary 条目,7cebdbf 刚修);`compact/model_window`(1M 模型不提前压缩,09cc16d 刚修) | 触发阈值边界(>= 语义)、boundary 序列化、compact 后请求的 messages 形状 | 阈值语义连续两轮出 bug,必须钉死 |

**每条轴的统一交付定义:**
- case.json + fixture + TS 录制的 golden(不可确定性录制的部分用请求侧快照代替,见 1.3);
- 暴露的 bug 修复 + `docs/alignment-findings.md` 台账更新;
- 真实差异走 `known_divergence` 机制,禁止改 golden 抹平。

### 1.2 差分 CI 化(nightly)

- [ ] 新增 `.github/workflows/nightly-differential.yml`:workflow_dispatch + cron(每日);步骤 = 装官方 `claude` CLI(固定版本号,见 1.4)→ `make e2e` → 上传失败 case 的两侧原始输出为 artifact。
- [ ] `make alignment-full`(519 py-only case)与 `make e2e` 一起进 nightly,PR CI 保持现状(快)。
- [ ] 失败时产出两侧 stdout/stderr/JSONL diff,不只 assert 消息。

### 1.3 请求侧快照常态化

输出对齐骗得过 fixture,骗不过请求体(系统提示 gap、工具注册表 gap 都是请求侧发现的)。把零散断言升级为**全量快照**:

- [ ] 新增 `tests/e2e/snapshots/request_envelope.json`:一次标准 `-p` 调用的完整请求体(in-process 捕获 `call_model` 入参),volatile 字段(uuid/时间/cwd)归一化后整体快照。
- [ ] 同样快照:system prompt 全文(分块)、tools 全量 schema、subagent 请求体。
- [ ] 快照更新必须人工 review(git diff 即 review 界面)——这就是穷人版 contract testing,也是 Part B「契约即数据」的第一块砖。

### 1.4 oracle 版本治理

- [ ] golden.json 增加元数据字段 `"oracle": {"cli": "claude", "version": "2.1.165", "recorded_at": "..."}`;`record_golden.py` 自动写入(读 `claude --version`)。
- [ ] `test_e2e_cases.py` 在 oracle 版本与 golden 记录不一致时打 warning(不 fail),重录时强制甄别漂移。
- [ ] 修复 recovered 2.1.88 TS CLI headless 卡死**降级为可选任务**:既有排查(profileCheckpoint 定位到 `await import('src/cli/print.js')` 模块加载链,Grove/telemetry/顶层 await 已排除)表明是 sourcemap 重建的深层产物,投入产出比差。替代策略:**版本漂移用 2.1.88 源码人工裁决**(差异出现时读 `recovered-from-cli-js-map/src/` 对应文件定案),裁决结果记入 `known_divergence` 或 findings 台账。

**Phase 1 验收门:**

```bash
make -C hare e2e          # 期望: ≥60 个 golden case 全绿(41 + 6 轴 × ≥3)
gh workflow run nightly-differential.yml && gh run watch  # 期望: 成功
pytest hare/tests/e2e/test_request_side_alignment.py -v   # 期望: 含全量快照断言,全绿
```

---

## Phase 2 · 深度补齐:让 oracle 拉动实现

> 不按"模块清单"补(会陷入 645 个 utils 文件的泥潭),按"差分轴暴露 → 修复 → 钉住"的循环补。本阶段同时处理评审发现的深度洼地。

### 2.1 对抗审计制度化(把"挖 bug 的人"变成"流程")

近 4 个 commit 证明对抗审计是目前发现真 bug 效率最高的手段。制度化为固定节奏:

- [ ] 每完成一条差分轴,跑一轮**对源码的对抗式核对**:选该轴涉及的 TS 文件,逐函数对照 Python 移植,产出 confirmed/refuted 清单(项目已有成熟做法,见 memory 2026-06-15:4-skeptic ultracode workflow 抓到 5 个真 bug)。
- [ ] 审计范围记录在 `hare/audit/<date>-<axis>.md`:哪些文件核对过、结论、遗留。
- [ ] **收敛判据(Phase 2 的门):连续 2 轮对抗审计(不同轴)零新 confirmed bug。**

### 2.2 深度洼地清偿(评审锚定的三处)

| 洼地 | 现状(5 月报告 + 本次评审) | 行动 | 判定 |
|---|---|---|---|
| bridge 编排 | 文件 30/31 对齐,编排逻辑 ~9% | **先拍板范围**:bridge 是远程会话(claude.ai 接管终端会话)功能,headless 对齐不依赖它。建议 SCOPE.md 定为 P3/不做,只保留类型与配置解析(已有) | 拍板即完成 |
| 瘦命令 | commands_impl 127 文件,部分为薄壳 | 跑 `make audit-sizediff` 产出 TS↔PY 行数比清单;headless 可达的命令(`--print` 下有效的:config/model/permissions 相关)优先补齐;纯 TUI 命令(statusline/themes 等)随 Phase 3 拍板 | sizediff 清单上 headless 命令行数比 ≥50% 或有豁免说明 |
| utils 深度 | 645 文件,5 月评 2.5/10,现已大幅增长 | 不做地毯式补齐;由差分轴与对抗审计拉动,命中哪个 utils 补哪个 | 随 2.1 收敛判据 |

### 2.3 mypy 棘轮(只降不升)

- [ ] `scripts/check_mypy_regression.py` 增加 `--ratchet` 语义:通过时若实测 < 基线,自动把新低值写回 Makefile(或提示手动更新);任何回升 FAIL。
- [ ] 目标节奏:每条差分轴收尾时顺手清该轴涉及模块的 mypy 错误;P0 模块(`query/`、`tools_impl/`、`services/api/`)在 Phase 2 结束时清零。

### 2.4 覆盖率去水分

- [ ] 删除或重写 coverage-chasing 测试文件(`test_hit_80` / `test_coverage_boost` / `test_branch_gap_close` 等,约 16 个,只执行不断言)。先删,跑 `make test-unit` 看真实覆盖率跌到多少,以真实值重设 `coverage-p0p1` 门。

**Phase 2 验收门:** 连续 2 轮对抗审计零新 confirmed bug;P0 模块 mypy=0;sizediff 清单无未豁免的 headless 命令洼地;覆盖率门基于去水分后的真实值。

---

## Phase 3 · 交互层决策(拍板优先,实施可选)

### 3.1 必做:SCOPE.md 拍板(半天)

创建 `hare/SCOPE.md`,逐条定级(建议值如下,由你终审):

```markdown
# hare 范围定义
| 子系统 | 级别 | 含义 |
|---|---|---|
| headless (-p / json / stream-json / resume / hooks / MCP / permissions) | A:行为等价 | 差分验收,bug 即缺陷 |
| 交互 REPL | B:简化替代 | 可用但不承诺等价;差距见 3.2 清单 |
| bridge 远程会话编排 | C:不做 | 保留类型/配置解析 |
| TUI(components/ink 全量)、teammate spawning、native-ts | C:不做 | — |
| sandbox 网络隔离(@anthropic-ai/sandbox-runtime 外部包) | C:不做 | 写限制近似已实现并声明 |
```

### 3.2 可选:REPL 升级为 Textual TUI(若 REPL 定为 B+/A-)

- 现状:`_run_repl`(main.py)是简化行式 REPL,`ink_facade.py` 是天然 seam。
- 若做:引入 `textual` 为 **可选依赖**(`pip install hare[tui]`,守住核心零依赖,见 Part B-4);ink 组件到 textual 的对应关系(Box→Container/Static、useInput→on_key、Spinner→LoadingIndicator);先做四件套:输入框+流式输出区+权限对话框+斜杠命令菜单。
- 交付判定:`python -m hare`(交互)冒烟脚本(pexpect 驱动:输入 prompt→看到流式输出→/exit)绿。

---

## Phase 4 · 发布验收

- [ ] 跑 `alignment/ALIGNMENT_DEFINITION.md` 的 release_ready 公式,逐项出示证据:P0/P1 case 100%、weighted ≥0.999、P0P1 行覆盖 ≥0.90 分支 ≥0.80(用去水分后的真实覆盖)、零 P0/P1 stub、bandit 高危零。
- [ ] `make freeze-baseline` 冻结基线;打 tag(建议 `v2.1.88-alpha1`,版本号已在 pyproject 对齐上游)。
- [ ] 更新 `REVIEW_*.md` 的后续版:记录验收时点的全部量化指标,作为下一轮的锚。

---

# Part B · 架构创新(四大结构性升级 + 两条守则)

> 定位:每一项都**针对评审发现的真实痛点**,且设计为不破坏"镜像 TS"结构的增量层,可在 Phase 1–2 期间并行落地地基,Phase 4 后全面展开。

## B-1 契约即数据(Behavior-Contract Layer)——把 oracle 从二进制变成版本化资产

**痛点(评审 §2.2-①②):** oracle 是"本机装的 claude 2.1.165 二进制"——版本漂移无法根治、CI 依赖外部安装、golden 的出处不可审计、换机器不可复现。

**设计:** 把"TS 原版在场景 X 下的行为"抽取为纯数据的**契约包**,与任何二进制解耦:

```
hare/contracts/2.1.88/
├── manifest.json            # oracle 出处: cli 版本/录制时间/环境指纹
├── golden/**                # 输出侧: 现有 41+ golden(迁移即得)
├── requests/**              # 请求侧: 信封/system prompt/tools schema 全量快照(1.3 的产物)
├── schemas/**               # 工具 input_schema 逐字段(已有审计结论,落成 JSON)
└── prompts/**               # 字面系统提示文本(memory_types 逐字教训的推广)
```

- 日常 CI **只对契约跑**,不需要 TS 二进制;`claude` CLI 只在"录制/升级契约"时需要。
- 契约目录整体入 git:每次重录都是一个可 review 的 diff——**升级 oracle 版本 = 一个 PR**,漂移逐条可见、可裁决(接受→更新契约;拒绝→known_divergence)。
- 未来对齐 2.1.165+ 时,开新目录 `contracts/2.1.165/`,多版本契约并存,hare 用 feature flag 选择目标版本——这是上游持续演进下唯一可持续的追赶模式。

**落地路径:** Phase 1 的 1.3/1.4 就是第一块砖(快照+版本元数据);Phase 2 收尾时把 `alignment/golden`、快照、schema 审计结论迁入 `contracts/` 统一布局。**工作量小(主要是搬家+manifest),收益是根治两个 P0 级验证痛点。**

## B-2 事件溯源会话内核(Event-Sourced Session Core)——消灭 resume 类 bug 的整个类别

**痛点(评审 §2.3-1):** resume 读路径缺失(0e8c226)、compact boundary 不持久化(7cebdbf)这类 bug 的共同根因:**运行时状态和 transcript 是两套真相,靠人肉保持同步**。写路径记了,读路径忘了;或反之。

**设计:** transcript JSONL 已经事实上是事件日志(`session_storage.py` 已有 chain 构建、compact boundary 条目、orphan tool_result 恢复)。把它**升格为唯一真相源**:

```python
# hare/state/session_events.py(新增,不动现有代码)
SessionEvent = UserTurn | AssistantTurn | ToolResult | CompactBoundary | ConfigChange | ...

def fold(events: Iterable[SessionEvent]) -> SessionState:
    """纯函数: 事件序列 → 引擎可用的完整会话状态(messages/token 计数/todo/权限缓存)"""
```

- **resume = replay**:`--continue`/`--resume` 不再是"另一条读路径",而是 `fold(load_events(session_id))`——写路径能产生的任何状态,读路径天然能恢复,**这一类 bug 结构性消失**。
- compact、fork、`--resume` 到历史某点、会话分叉,全部变成事件流上的免费操作。
- **不变量可属性测试**:`fold(write_then_load(events)) == fold(events)`(roundtrip 恒等),用 Hypothesis 生成任意事件序列——这是现有 3 个属性测试文件之后最有价值的第 4 个。

**落地路径(渐进,不破坏对齐):** ① Phase 2 期间先写 `SessionEvent` 类型 + `fold()` + roundtrip 属性测试,**只读不接管**(fold 结果与现有 resume 路径的结果做等价断言,当差分用);② 等价断言稳定后,让 resume 路径改调 fold,旧路径删除;③ TS 侧 JSONL schema 由 B-1 契约钉住,保证序列化不漂移。

## B-3 纯内核 + 端口(Agent Kernel / Ports)——把对抗审计自动化成确定性仿真

**痛点(评审 §2.2-③):** "每轮对抗审计都挖出 bug"说明未测路径(故障、并发、取消、边界时序)是主要雷区。人肉审计不可持续,需要机器化的探索手段。

**设计:** `query/deps.py` 的 `QueryDeps`(4 个注入点)已经证明了这个模式——泛化为完整端口层:

```python
# hare/query/ports.py(扩展 QueryDeps,保持向后兼容)
@dataclass
class Ports:
    model: CallModel          # 已有(call_model)
    compact: CompactOps       # 已有(micro/auto)
    clock: Callable[[], float]        # 新: time.time 可注入
    fs: FsPort                        # 新: read/write/glob 收口
    proc: ProcPort                    # 新: 子进程执行收口(BashTool 底座)
    rand: Callable[[], str]           # 已有(uuid)
```

内核(query loop + 工具调度 + 权限判定)只经端口触碰外界 → 可做 **FoundationDB 式确定性仿真测试**:

```python
# tests/simulation/ 的形状
def test_simulated_session(seed):
    ports = SimPorts(seed)   # 确定性伪随机: API 429/500/断流/工具超时/慢盘/取消
    run_session(script=random_session(seed), ports=ports)
    assert invariants(ports.journal)   # 不变量: 无未闭合 tool_use、JSONL 可 fold、
                                       # 权限拒绝后无副作用、取消后无泄漏子进程…
```

- 千级 seed 每晚跑,失败 seed 100% 可复现——**把"对抗审计挖 bug"变成"仿真器每晚自动挖"**。
- 与 B-2 协同:不变量大多定义在事件流上,fold 是天然的断言载体。

**落地路径:** ① Phase 1 顺手把 clock/rand 收进 deps(改动极小);② Phase 2 把 BashTool/FileTools 的执行底座指向 proc/fs 端口(镜像结构不动,只换 import);③ Phase 4 后建 `tests/simulation/`,从 3 个不变量起步(tool_use 闭合、JSONL roundtrip、denial 无副作用)。

## B-4 零依赖内核 + 分层发行——把现状的偶然优势正式化

**现状即资产:** `pyproject.toml` 里 `dependencies = []`——核心是纯 stdlib,连 anthropic SDK 都是可选的。这是 TS 原版(数百个 npm 依赖)不具备的结构性优势:审计面小、安装即 `pip install`、嵌入友好(hare 可以作为库被导入,TS 版做不到)。

**正式化为承诺:**

| 发行层 | 内容 | 依赖 |
|---|---|---|
| `hare`(core) | 内核+工具+headless CLI | **零**(承诺写进 CI:一个在无三方包 venv 里跑单测的 job) |
| `hare[anthropic]` | 官方 SDK 传输 | anthropic |
| `hare[tui]` | Textual 交互层(Phase 3) | textual |
| `hare[dev]` | 现状 | pytest 等 |

- 顺带打开一个 TS 版没有的产品方向:**`import hare` 作为 Python 原生 agent 库**(`hare.query()` 直接可用,fixture 注入即测试)——SDK 不是移植目标,而是架构副产品。

## 守则一:不要现在做的"创新"(YAGNI 防线)

| 诱惑 | 为什么不 |
|---|---|
| 全库改 pydantic / 全库 async 换 anyio / 目录 Python 化重构 | 破坏镜像结构 → `Port of:` 溯源失效 → 对抗审计(当前最有效的质量手段)成本暴涨。Phase 4 验收后再议 |
| 自研 TUI 框架 / 提前做 B-3 全量仿真器 | 交互层还没拍板范围;仿真器依赖端口层就位,跳步会做成 mock 泥潭 |
| 追新版(2.1.165+)双线对齐 | 先过 2.1.88 验收;多版本追赶交给 B-1 的契约目录机制 |

## 守则二:创新与阶段的绑定关系(什么时候动手)

```
Phase 1 ──── B-1 地基(请求快照+oracle 元数据)、B-3 地基(clock/rand 进 deps)
Phase 2 ──── B-2 地基(SessionEvent+fold+roundtrip 属性测试,只读模式)
Phase 3 ──── B-4 的 [tui] 层(若拍板做)
Phase 4 后 ── B-1 契约目录统一、B-2 接管 resume、B-3 仿真套件、B-4 发行承诺进 CI
```

---

# Part C · 风险与里程碑

## 风险表

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 463 文件未提交期间发生工作区损坏 | 低 | 灾难 | Phase 0 Task 0.1 今天做 |
| oracle 版本漂移把假差异当真 bug 修 | 中 | 中 | 1.4 版本元数据 + 2.1.88 源码人工裁决 + B-1 契约化 |
| 差分轴暴露的 bug 量超预期,Phase 1 膨胀 | 中 | 中 | 每轴 timebox;bug 修不完的记 known_divergence/findings 台账,不阻塞轴推进 |
| record_golden 依赖本机 claude 登录态,CI 难复制 | 中 | 中 | golden 入库后 CI 只比对不录制;录制永远是本地人工步骤 |
| 覆盖率去水分后数字大跌引发误判 | 高 | 低 | 提前声明:跌是挤水分,门以真实值重设 |
| B-2/B-3 改造引入回归 | 低 | 高 | 全部设计为"先并行只读、等价断言稳定再接管";任何一步 e2e 必须全绿 |

## 里程碑(以周为单位,从 Phase 0 完成日起算)

| 周 | 交付 |
|---|---|
| W0(1 天) | Phase 0 全部;git 干净、mypy 门有效、文档指路 |
| W1-W3 | Phase 1:六轴差分(resume/subagent/hooks 先行)+ nightly CI + 请求快照 + oracle 元数据 |
| W4-W8 | Phase 2:对抗审计×N 轮 → 收敛;mypy P0 清零;覆盖去水分;SCOPE.md 拍板(W4 即做) |
| W6-W9(并行) | Phase 3(若做 TUI):Textual 四件套 |
| W9-W10 | Phase 4:release_ready 全项验收、freeze baseline、tag `v2.1.88-alpha1` |
| W10+ | Part B 全面展开:契约目录统一 → 事件溯源接管 resume → 仿真套件 → 零依赖承诺进 CI |

---

## 附:本路线图的展开纪律

每个进入执行的任务组,先用 writing-plans 展开成本目录下的独立计划(含失败测试→实现→提交的完整步骤),再用 subagent-driven-development 或 executing-plans 执行。本文档只更新两处:阶段验收门的勾选状态、风险表。
