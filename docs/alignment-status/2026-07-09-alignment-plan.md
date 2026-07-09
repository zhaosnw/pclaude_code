# Hare Alignment Plan - 2026-07-09

## 目标

这份方案只描述从当前工作区继续推进 Python 版 `hare` 与 recovered TS/JS 资产对齐的后续路线。目标不是一次性把所有差异“拍平”，而是把对齐工作拆成可验证、可回滚、可提交的小阶段。

当前已经完成的结构性收口：

- root `alignment/` 兼容层已经删除。
- E2E fixture 路径已经统一到 `hare/alignment/fixtures/...`。
- `hare/alignment/` 是当前 golden E2E 主资产目录。
- `hare/hare/` 已经从完整镜像源码树彻底删除。
- `legacy_alignment` 中的 Python 路径已经从 `hare/hare/...` 收口到 `hare/...`。

后续对齐的主线是：先稳住结构和验证边界，再补真差分覆盖，最后按模块消化行为差异和类型债。

## 对齐原则

1. **只认一个 canonical Python 源码树。**
   顶层 `hare/` 是唯一主源码树；`hare/hare/` 不再存在，也不再作为受支持入口。

2. **只认一个 golden E2E 主资产目录。**
   新 case、fixture、golden、seed 都进入 `hare/alignment/`；`legacy_alignment/` 只保留旧 Phase1/py-only oracle 资产，除非明确迁移。

3. **先加观测，再改行为。**
   对 CLI、session、MCP、hooks、permission、compact 这类高风险路径，先补 fixture/golden 或 request-side 检查，再改实现。

4. **每一步都要有可复跑的验收命令。**
   不能只靠肉眼 diff。每个阶段结束时都应该有一组固定命令证明边界没有倒退。

5. **不再扩大重复资产面。**
   新增对齐材料时，优先补在 canonical 位置；如果必须保留兼容入口，要同时加守卫测试。

## 阶段 0：固定当前基线

### 目标

把当前“已收口后的状态”固定成可引用的基线，避免后续对齐时不知道自己从哪里出发。

### 任务

1. 更新 `REVIEW_2026-07-02.md`，确保它只描述当前仍成立的状态。
2. 更新 `docs/alignment-status/2026-07-08.md` 或新建快照，记录当前两次结构性提交后的验证结果。
3. 为后续计划保留这份 `2026-07-09-alignment-plan.md` 作为执行清单。

### 验收

```bash
git status --short --branch
make alignment-guardrails
python scripts/verify_alignment.py
python scripts/gen_alignment_priority.py
python -m pytest tests/test_alignment_scripts.py hare/tests/test_hare_alignment_scripts.py -q
```

### 完成标准

- 文档不再说 root `alignment/` 仍存在。
- 文档不再说 `hare/hare/` 是完整镜像源码树。
- 上述命令全部通过。

## 阶段 1：清理残留路径语义

### 目标

让仓库里的活代码、活测试、活文档都只表达当前 canonical 路径。

### 任务

1. 扫描 `hare/hare/` 残留引用。
   保留的引用只允许出现在：
   - 当前迁移说明
   - 历史计划或旧审计记录
   - 明确标为历史的文档段落

2. 扫描 root `alignment/` 残留引用。
   保留的引用只允许出现在：
   - 历史计划
   - 已废弃 checklist
   - 明确说明“已删除”的当前状态文档

3. 确认 root-only 工作流已经足够。
   重点检查：
   - `pyproject.toml`
   - root `Makefile`
   - root `tests/`
   - root `scripts/`

### 验收

```bash
rg -n 'hare/hare/' . \
  -g '!recovered-from-cli-js-map/**' \
  -g '!**/.git/**' \
  -g '!docs/superpowers/**' \
  -g '!audit/**' \
  -g '!hare/audit/**'

rg -n '(^|[^[:alnum:]_])alignment/(cases|fixtures|golden|seeds)' . \
  -g '!docs/superpowers/**' \
  -g '!**/.git/**'

python - <<'PY'
import importlib
import hare
print(hare.__file__)
for name in ["hare.query_engine", "hare.session_setup", "hare.utils.session_storage"]:
    module = importlib.import_module(name)
    print(name, module.__file__)
PY
```

### 完成标准

- `hare.query_engine`、`hare.session_setup`、`hare.utils.session_storage` 都解析到顶层 `hare/...`。
- 活代码不再把 `hare/hare/...` 当作 canonical 路径。
- 活测试不再依赖 root `alignment/`。
- 计划中不再要求保留 `cd hare/` 或 `make -C hare ...` 入口。

## 阶段 2：收紧 guardrails

### 目标

把“不能倒退”的边界写成测试，而不是只写在文档里。

### 任务

1. 增加 `hare/hare/` 删除守卫。
   检查 `hare/hare/` 下不再出现任何 Python 运行时代码文件。

2. 增加 canonical import 守卫。
   在 root 工作目录视角下，确认核心模块解析到顶层 `hare/...`。

3. 增加 legacy path 守卫。
   确认 `legacy_alignment/**/*.json` 里的 Python 路径不再出现 `hare/hare/`。

4. 把这些守卫接入 `alignment-guardrails`。

### 建议测试文件

- `tests/test_hare_tree_cleanup_guard.py`

### 验收

```bash
make alignment-guardrails
python -m pytest tests/test_alignment_scripts.py hare/tests/test_hare_alignment_scripts.py -q
```

### 完成标准

- 如果有人重新添加 `hare/hare/query_engine.py` 这类镜像文件，测试会失败。
- 如果有人把 `legacy_alignment` 路径写回 `hare/hare/...`，测试会失败。
- 如果 root 视角 import 不再命中顶层 `hare/...`，测试会失败。

## 阶段 3：重新定义 legacy_alignment 的角色

### 目标

把 `legacy_alignment/` 从“旧资产堆”变成明确的参考资产层，避免它继续影响主线判断。

### 任务

1. 给 `legacy_alignment/` 写职责说明。
   明确它是旧 Phase1/py-only oracle，不是 golden E2E 主入口。

2. 分类现有 1892 rows。
   至少分成：
   - 已由 `hare/alignment/` golden E2E 覆盖
   - 仍有价值但未迁移
   - 只适合作为静态参考
   - 可归档或删除候选

3. 给 `alignment_data.json` 增加或复用字段表达迁移状态。
   建议字段：
   - `golden_status`
   - `migration_target`
   - `migration_note`

4. 生成迁移优先级列表。
   优先 P0/P1，且优先能暴露真实行为差异的入口。

### 验收

```bash
python scripts/verify_alignment.py
python scripts/gen_alignment_priority.py
python scripts/check_alignment_regressions.py
```

### 完成标准

- `legacy_alignment` 的用途在文档中明确。
- P0/P1 rows 有清晰的迁移状态。
- 后续新增 golden E2E case 能反向标记对应 legacy row。

## 阶段 4：补真差分覆盖

### 目标

把当前“主链路能跑”推进到“关键行为差异能被稳定抓住”。

### 优先覆盖轴

1. **CLI print / JSON / stream-json**
   覆盖：
   - `-p`
   - `--output-format json`
   - `--output-format stream-json`
   - 非零退出
   - stderr / stdout 边界

2. **session resume / continue**
   覆盖：
   - 新会话 transcript 写入
   - `--resume`
   - `--continue`
   - session id / cwd / project scope
   - transcript JSONL schema

3. **permission modes × settings**
   覆盖：
   - allow
   - deny
   - ask
   - bypassPermissions
   - settings 文件优先级
   - tool-specific rules

4. **tools**
   覆盖：
   - Read / Write / Edit / MultiEdit
   - Bash
   - Glob / Grep / LS
   - parallel tool calls
   - tool error serialization

5. **MCP**
   覆盖：
   - config parse
   - stdio server lifecycle
   - tool listing
   - tool invocation
   - auth / header / env expansion

6. **hooks**
   覆盖：
   - pre-tool hooks
   - post-tool hooks
   - stop hooks
   - block vs allow
   - hook output serialization

7. **compact / auto-compact**
   覆盖：
   - manual compact
   - token threshold trigger
   - max output handling
   - post-compact message shape

8. **subagent / Agent**
   覆盖：
   - nested query loop
   - tool exposure boundary
   - transcript relationship
   - error propagation

### 每个 case 的落地格式

每个新 case 至少包含：

- `hare/alignment/cases/<group>/<name>/case.json`
- `hare/alignment/fixtures/<name>.json`
- `hare/alignment/golden/<group>/<name>/golden.json`
- 如果需要文件系统输入，放入 `hare/alignment/seeds/`

### 录制流程

```bash
python scripts/record_golden.py <case_id>
python -m pytest tests/e2e -k <case_id> -q
```

### 完成标准

- 每个高优先级行为轴至少有 1 个 golden E2E case。
- 新 case 不依赖网络、不写真实用户目录、不读取真实 `~/.hare/`。
- 差异必须通过 `known_divergence` 或修实现来处理，不能靠 normalizer 抹掉真实行为差异。

## 阶段 5：按模块清偿行为差异

### 目标

把差分 case 暴露出来的 bug 转化为可追踪的小修复，而不是靠大范围重写。

### 工作方式

1. 选一个行为轴。
2. 先补或确认 golden E2E。
3. 跑出失败。
4. 对照 recovered TS/JS 行为。
5. 最小范围修改 Python 实现。
6. 增加或更新 targeted unit test。
7. 复跑该轴 E2E 和相关单测。

### 推荐顺序

1. `CLI output`
2. `session persistence`
3. `permissions`
4. `tools`
5. `hooks`
6. `MCP`
7. `compact`
8. `subagent / Agent`

### 验收

每次修复至少跑：

```bash
python -m pytest tests/e2e -q
make alignment-guardrails
python scripts/check_mypy_regression.py --baseline 520
```

视模块增加：

```bash
python -m pytest hare/tests/test_hare_<module>.py -q
python -m pytest tests/test_<module>.py -q
```

### 完成标准

- 修复能被一个或多个 golden E2E case 证明。
- 不扩大 normalizer。
- 不新增 `mypy` 错误。
- 不新增 `NotImplementedError`。

## 阶段 6：mypy 类型债治理

### 目标

保持 `mypy` 回归门有效，并逐步把 `520` 基线向下压。

### 任务

1. 保持当前 scoped gate。
   当前命令：

```bash
python scripts/check_mypy_regression.py --baseline 520
make mypy-regression
```

2. 按模块降基线。
   每次选择一个模块，例如：
   - `utils/settings`
   - `query`
   - `services/mcp`
   - `tools`
   - `cli`

3. 每完成一组修复，更新 baseline。
   只能向下改，不能向上放宽。

4. 为高风险类型修复补运行测试。
   类型修复如果触及运行逻辑，必须跑对应单测和 E2E。

### 完成标准

- `mypy` baseline 单调下降。
- `make mypy-regression` 持续通过。
- 类型修复不改变已有 runtime 行为，除非有测试证明这是预期修复。

## 阶段 7：最终收口与发布前检查

### 目标

在结构、行为、类型三条线都稳定后，形成一个可以推送/发 PR 的状态。

### 最终检查清单

```bash
git status --short --branch
make alignment-guardrails
python -m pytest tests/e2e -q
python scripts/verify_alignment.py
python scripts/check_mypy_regression.py --baseline 520
python scripts/detect_stubs.py
```

如果时间允许，再跑：

```bash
make test-unit
make test-integration
make test-alignment
```

### 完成标准

- 工作树只包含当前阶段相关改动。
- 当前状态文档已更新。
- 每个提交只包含一个清晰主题。
- 推送前没有 `node_modules/`、缓存、coverage、临时文件进入 git。

## 建议执行顺序

1. **先做阶段 0 和阶段 1。**
   这是地基，风险低，能减少后续误判。

2. **再做阶段 2。**
   把刚收口的结构写进 guardrails，防止回退。

3. **然后做阶段 4。**
   先补真差分覆盖，尤其是 session、permission、tools、MCP。

4. **阶段 5 和阶段 6 交替推进。**
   行为 bug 和类型债最好按模块一起收，但每次提交保持小。

5. **阶段 3 可以穿插做。**
   `legacy_alignment` 的治理不必阻塞 golden E2E，但需要逐步让它从“旧堆栈”变成“可查询的参考层”。

## 近期第一批建议任务

1. 增加 `hare/hare/` 清理 guard 测试，并接入 `alignment-guardrails`。
2. 更新当前状态快照，记录 `80411c56` 之后的真实结构。
3. 选 `session resume / continue` 作为第一条真差分轴，补 2 到 3 个 golden E2E case。
4. 选 `permission modes × settings` 作为第二条真差分轴，补 allow/deny/ask 的最小闭环。
5. 对第一批 case 暴露的问题做小步修复，每个修复都带 targeted test。

## 暂停点

遇到下面情况时先停下来复盘，不要继续堆改动：

- 需要改 normalizer 才能让 case 通过。
- 新 case 会访问真实网络或真实用户目录。
- `legacy_alignment` 与 `hare/alignment` 对同一行为给出相反结论。
- `mypy` baseline 需要上调。
- 删除 `hare/hare/` 后，root-only 工作流之外还有未识别的强依赖入口。
- 新增对齐资产必须复制到两个目录才能跑通。
