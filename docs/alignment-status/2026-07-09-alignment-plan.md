# Hare Alignment Plan - 2026-07-09（v2 扩写版）

## 目标

终态定义：**一个与 recovered TS/JS 参考实现功能对齐、可以在其上持续迭代的 Python code agent。**

这意味着两件事同时成立：

1. **行为对齐可证明。** 每一块已声明对齐的功能，都有 golden E2E case（TS reference 录制的 oracle）作为证据，而不是"看起来差不多"。
2. **对齐进度可度量。** 有一份从 recovered TS/JS 机械提取的功能清单（parity matrix），每一项都标注对齐状态，能随时回答"离功能对齐还差多少"。

本计划的主线是 **阶段 3（parity matrix）→ 阶段 4（真差分覆盖）→ 阶段 5（行为清偿）→ 阶段 6（dogfood）** 的循环。阶段 0/1/2 是一次性的地基工作，要快速过掉；阶段 7（mypy）只保门不主动清偿；阶段 8 是收口检查。

当前已经完成的结构性收口：

- root `alignment/` 兼容层已经删除。
- E2E fixture 路径已经统一到 `hare/alignment/fixtures/...`。
- `hare/alignment/` 是当前 golden E2E 主资产目录。
- `hare/hare/` 已经从完整镜像源码树彻底删除（残留 `__pycache__` 待清理）。
- `legacy_alignment` 中的 Python 路径已经从 `hare/hare/...` 收口到 `hare/...`。
- `tests/test_hare_tree_cleanup_guard.py` 已存在，含 1 个 `hare/hare/` 无 Python 文件守卫。

## 对齐原则

1. **只认一个 canonical Python 源码树。**
   顶层 `hare/` 是唯一主源码树；`hare/hare/` 不再存在，也不再作为受支持入口。

2. **只认一个 golden E2E 主资产目录。**
   新 case、fixture、golden、seed 都进入 `hare/alignment/`；`legacy_alignment/` 只作为归档参考层。

3. **只认 TS reference 作为 oracle。**
   golden 必须通过 `scripts/record_golden.py` 从 TS 参考 CLI（`CLAUDE_TS_CLI`）录制（`policy.golden_source: "ts-reference"`），禁止用 Python 实现自己录自己。

4. **Golden 优先于存量单测。**
   当 golden E2E（TS oracle）与存量单测断言冲突时，golden 赢：按 TS 行为修实现，同步改掉锁旧行为的单测。存量覆盖率导向测试（`test_hare_hit_80.py`、`test_hare_coverage_*.py`、`test_hare_branch_*.py` 等）锁的是 Python 现状而非 JS 正确行为，不构成"不能改行为"的理由。

5. **先加观测，再改行为。**
   对 CLI、session、MCP、hooks、permission、compact 这类高风险路径，先补 fixture/golden 或 request-side 检查，再改实现。

6. **每一步都要有可复跑的验收命令。**
   不能只靠肉眼 diff。每个阶段结束时都应该有一组固定命令证明边界没有倒退。

7. **不再扩大重复资产面。**
   新增对齐材料时，只补在 canonical 位置；差异必须通过 `known_divergence` 或修实现来处理，不能靠扩大 normalizer 抹掉真实行为差异。

## 当前基线事实（写死，供后续阶段引用）

### golden E2E 资产现状（41 个 case）

| 分组 | 数量 | 覆盖内容 |
|---|---|---|
| `chat/` | 12 | 单轮/多轮文本、thinking、unicode、CRLF、空文本、`--output-format json` / `stream-json` 基础输出 |
| `cli/` | 3 | `--help`、`--version`、bad flag |
| `json/` | 5 | json 输出、tool_use、多轮 usage、特殊字符、长 tool 链 |
| `limits/` | 1 | `--max-turns` |
| `permission/` | 2 | deny Bash、permission_denials 字段 |
| `stream_json_tools/` | 1 | stream-json 下的 Read |
| `tools/` | 17 | Bash/Read/Write/Edit/MultiEdit/Glob/Grep/LS/TodoWrite、并行调用、非零退出、错误序列化 |

**完全没有 golden 覆盖的轴**：session resume/continue、settings 优先级、hooks、MCP、compact、subagent/Agent、stream-json 输入侧。

### 基础设施现状

- oracle 录制：`scripts/record_golden.py`，用法 `CLAUDE_TS_CLI="bun <repo>/recovered-from-cli-js-map/src/entrypoints/cli.tsx" python scripts/record_golden.py <case_id>`；起 mock Anthropic server（`scripts/mock_anthropic_server.py`），把 TS CLI 的 `ANTHROPIC_BASE_URL` 指过去，normalize 后写 golden。
- 比对入口：`tests/e2e/test_e2e_cases.py`，遍历 `hare/alignment/cases/**/case.json`，跑 `python -m hare`，支持 `known_divergence` → xfail。
- **runner 限制**：`scripts/e2e_runner.py` 每个 case 只支持一次 `argv` 调用（`e2e_runner.py:129`）。session resume/continue 需要先扩展 runner（见阶段 4.0）。
- fixture 格式：`{"kind": "scripted", "responses": [{stop_reason, content, usage}, ...]}`，按请求顺序回放。
- guardrails：`make alignment-guardrails`（12 passed），主要覆盖 record_golden / e2e case / e2e_runner 的 canonical 路径约束。
- mypy 门：`python scripts/check_mypy_regression.py --baseline 520`。
- TS reference：`recovered-from-cli-js-map/`，版本 `2.1.88-recovered`，入口 `src/entrypoints/cli.tsx`，用 `bun` 直接跑 `.tsx`（参考 `start-recovered.ps1`），需 `--no-chrome`，建议加 `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`、`DISABLE_AUTOUPDATER=1`、隔离 `CLAUDE_CONFIG_DIR`。

## 阶段 0：固定基线 + 清理工作区垃圾（预计 0.5 天）

### 目标

把"已收口后的状态"固定成可引用的基线，同时清掉会持续制造误判的垃圾文件。

### 任务

1. **删除 heredoc 写坏产生的垃圾目录**（均未被 git 跟踪，直接删）：

```bash
cd /Users/midea/midea/pclaude_code
rm -rf 'hare/import asyncio,json,os,sys;sys.path.insert(0,"scripts");from alignment_mocks import run_query_case;count=0'
rm -rf 'hare/for r,d,f in os.walk(f"..'
rm -rf hare/hare/            # 只剩 __pycache__
rm -f hare/coverage.xml coverage.xml   # 已被 .gitignore 覆盖，本地清掉即可
```

2. **把误提交的 egg-info 移出 git 跟踪**（当前 `hare.egg-info/` 与 `hare/hare.egg-info/` 都被跟踪）：

```bash
git rm -r --cached hare.egg-info hare/hare.egg-info
printf '%s\n' '*.egg-info/' >> .gitignore
git add .gitignore
git commit -m "chore: untrack egg-info build artifacts"
```

3. 更新 `REVIEW_2026-07-02.md`，确保它只描述当前仍成立的状态。
4. 在 `docs/alignment-status/` 新建当日快照，记录两次结构性提交（`80411c56`、`db04b013`）之后的验证结果。

### 验收

```bash
git status --short --branch          # 干净，无垃圾目录
git ls-files | grep egg-info         # 无输出
make alignment-guardrails
python scripts/verify_alignment.py
python -m pytest tests/test_alignment_scripts.py hare/tests/test_hare_alignment_scripts.py -q
```

### 完成标准

- `hare/` 下不再有 heredoc 垃圾目录、`hare/hare/`、本地 coverage.xml。
- egg-info 不再被 git 跟踪。
- 文档不再声称 root `alignment/` 或 `hare/hare/` 镜像树仍存在。
- 上述命令全部通过。

## 阶段 1：清理残留路径语义（预计 0.5 天）

### 目标

让仓库里的活代码、活测试、活文档都只表达当前 canonical 路径。

### 任务

1. 扫描 `hare/hare/` 残留引用，保留的引用只允许出现在：当前迁移说明、历史计划或旧审计记录、明确标为历史的文档段落。
2. 扫描 root `alignment/` 残留引用，保留的引用只允许出现在：历史计划、已废弃 checklist、明确说明"已删除"的当前状态文档。
3. 确认 root-only 工作流已经足够，重点检查 `pyproject.toml`、root `Makefile`、root `tests/`、root `scripts/`。

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
- 活代码、活测试不再依赖 `hare/hare/` 或 root `alignment/`。
- 计划中不再要求保留 `cd hare/` 或 `make -C hare ...` 入口。

## 阶段 2：收紧 guardrails（预计 0.5 天）

### 目标

把"不能倒退"的边界写成测试。`tests/test_hare_tree_cleanup_guard.py` 已有第 1 个守卫（`test_hare_hare_tree_has_no_python_files`），在同一文件补齐其余守卫。

### 任务

在 `tests/test_hare_tree_cleanup_guard.py` 中新增：

1. **canonical import 守卫**：

```python
def test_core_modules_resolve_to_top_level_hare() -> None:
    import hare.query_engine, hare.session_setup, hare.utils.session_storage
    for mod in (hare.query_engine, hare.session_setup, hare.utils.session_storage):
        path = Path(mod.__file__).resolve()
        assert REPO_ROOT / "hare" in path.parents
        assert REPO_ROOT / "hare" / "hare" not in path.parents
```

2. **legacy path 守卫**：`legacy_alignment/**/*.json` 中的 Python 路径不再出现 `hare/hare/`。

```python
def test_legacy_alignment_has_no_mirrored_paths() -> None:
    offenders = [
        p for p in (REPO_ROOT / "legacy_alignment").rglob("*.json")
        if "hare/hare/" in p.read_text(encoding="utf-8")
    ]
    assert offenders == []
```

3. **golden oracle 守卫**（新增，对应原则 3）：所有 `hare/alignment/cases/**/case.json` 的 `policy.golden_source` 必须是 `"ts-reference"`；例外必须显式写 `known_divergence`。

4. 把以上守卫接入 root `Makefile` 的 `alignment-guardrails` 目标。

### 验收

```bash
make alignment-guardrails
python -m pytest tests/test_hare_tree_cleanup_guard.py -q
python -m pytest tests/test_alignment_scripts.py hare/tests/test_hare_alignment_scripts.py -q
```

### 完成标准

- 重新添加 `hare/hare/query_engine.py` 会失败。
- 把 `legacy_alignment` 路径写回 `hare/hare/...` 会失败。
- 新增非 ts-reference oracle 的 case 会失败。

## 阶段 3：Parity Matrix —— 定义"功能对齐"的范围与进度（预计 2-3 天）

### 目标

从 recovered TS/JS 机械提取功能清单，形成对齐工作的北极星。没有这一步，阶段 4/5 的"补覆盖"就没有完成度的概念。

### 任务

1. **提取功能清单。** 新建 `scripts/gen_parity_matrix.py`，从以下源头提取：

   | 维度 | TS 源 | 提取方式 |
   |---|---|---|
   | CLI flags / 子命令 | `recovered-from-cli-js-map/src/entrypoints/cli.tsx`（commander 定义） | 解析 `.option(` / `.command(` 调用 |
   | slash commands | `recovered-from-cli-js-map/src/commands/` | 目录文件名 + 导出的 command 名 |
   | 工具及 schema | `recovered-from-cli-js-map/src/tools/` | 工具名 + input schema 字段 |
   | hook 事件 | `recovered-from-cli-js-map/src/hooks/` | 事件类型枚举 |
   | settings 键 | `recovered-from-cli-js-map/src/utils/`（settings schema） | 键名清单 |

2. **生成矩阵文档** `docs/alignment-status/parity-matrix.md`，每行一个功能项，字段：

   - `feature`（如 `cli.--resume`、`tool.Bash.run_in_background`、`hook.PreToolUse`）
   - `status`：`aligned`（有 golden 证明）/ `implemented-unverified`（Python 有实现但无 golden）/ `missing` / `wont-align`（明确不做，写原因）
   - `evidence`：对应 golden case_id 或说明
   - `priority`：P0（print-mode code agent 主链路）/ P1（session、permission、hooks、MCP）/ P2（交互 UI、遥测等）

3. **把 status 判定接进验证。** `scripts/gen_parity_matrix.py --check` 校验矩阵里每个 `aligned` 项都有对应存在的 golden case，接入 `alignment-guardrails`。

4. **legacy_alignment 归档化**（原阶段 3 降级为一次性动作）：
   - 在 `legacy_alignment/README.md` 写明职责：旧 Phase1 / py-only oracle，只读参考层，不再新增，不阻塞主线。
   - 不做 1892 rows 的逐行分类打标；只在阶段 4 补某条轴时按需回查。
   - `alignment_data.json` 不再新增字段。

### 验收

```bash
python scripts/gen_parity_matrix.py            # 生成/刷新矩阵
python scripts/gen_parity_matrix.py --check    # aligned 项 golden 存在性校验
make alignment-guardrails
```

### 完成标准

- `parity-matrix.md` 存在且由脚本生成，能统计各 status 的数量（这就是对齐完成度指标）。
- 每个 P0/P1 功能项有明确 status。
- `legacy_alignment` 职责在其 README 中明确，不再出现在主线任务里。

## 阶段 4：补真差分覆盖（主线，与阶段 5 交替，按轴推进）

### 目标

把"主链路能跑"推进到"关键行为差异能被稳定抓住"。每条轴的工作方式统一：写 case → 用 TS reference 录 golden → 跑 Python 比对 → 差异进入阶段 5。

### 阶段 4.0：基础设施前置（先做，约 1 天）

1. **确认 TS reference 在本机可跑**（一切录制的前提）：

```bash
cd recovered-from-cli-js-map
bun install
CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 DISABLE_AUTOUPDATER=1 \
  bun src/entrypoints/cli.tsx --no-chrome --version
```

   如果 `bun` 跑不通，修 `local-shims/` 或记录阻塞点，**先解决这个再往下走**。

2. **扩展 e2e_runner 支持多次调用**（session resume/continue 需要）。在 case schema 中新增 `invocations`，与现有单 `entrypoint` 互斥：

```json
{
  "case_id": "session.resume_basic",
  "invocations": [
    {"argv": ["-p", "remember the word pineapple", "--output-format", "json"]},
    {"argv": ["--resume", "${session_id[0]}", "-p", "what word?", "--output-format", "json"]}
  ]
}
```

   约定：`${session_id[N]}` 从第 N 次调用的 JSON stdout 的 `session_id` 字段取值注入。`scripts/e2e_runner.py` 与 `scripts/record_golden.py` 同步支持（TS 侧同样两次调用、同样注入）。所有调用共享同一个临时 `CLAUDE_CONFIG_DIR` / `HARE_CONFIG_DIR` 与 cwd 沙箱。

3. **mock MCP stdio server 种子**：在 `hare/alignment/seeds/mcp_echo_server.py` 放一个最小 stdio MCP server（响应 `initialize`、`tools/list`、`tools/call`，提供一个 `echo` 工具），供 MCP 轴的 case 以 seed + `.mcp.json` 方式使用。

### 阶段 4.1 起：按轴补 case（每轴 2-5 个，先深后广）

推荐顺序与具体 case 清单：

**轴 1：session resume / continue（第一优先）**

| case_id | 内容 | 验证点 |
|---|---|---|
| `session.transcript_write` | 单次 `-p`，检查 transcript JSONL 落盘 | 文件路径 project-scope、JSONL schema 字段集 |
| `session.resume_basic` | 两次调用，第二次 `--resume <id>` | 上下文延续、session_id 不变 |
| `session.continue_basic` | 两次调用，第二次 `--continue` | 取最近会话 |
| `session.resume_bad_id` | `--resume` 不存在的 id | 退出码 + stderr 措辞 |

**轴 2：permission modes × settings（第二优先）**

| case_id | 内容 |
|---|---|
| `permission.settings_allow_bash` | seed 里 settings.json `permissions.allow: ["Bash(echo *)"]`，工具放行 |
| `permission.settings_deny_read` | deny 规则拦截 Read |
| `permission.mode_bypass` | `--permission-mode bypassPermissions` |
| `permission.settings_precedence` | project settings 与 user settings 冲突时的优先级 |

settings 通过 `fs.seed` 放进沙箱 cwd 的 `.claude/settings.json`，user 级配置通过 case 的 `env` 指到临时 `CLAUDE_CONFIG_DIR`。

**轴 3：hooks**

| case_id | 内容 |
|---|---|
| `hooks.pretool_block` | PreToolUse hook 返回 block，工具不执行 |
| `hooks.pretool_allow` | PreToolUse 放行 |
| `hooks.posttool_output` | PostToolUse hook 的输出注入 |
| `hooks.stop_hook` | Stop hook 触发 |

hook 命令用 seed 里的小 shell/python 脚本，输出写沙箱内文件以便 `snapshot_files` 断言副作用。

**轴 4：MCP**

| case_id | 内容 |
|---|---|
| `mcp.config_parse_error` | 坏 `.mcp.json` 的报错行为 |
| `mcp.stdio_tool_list` | `--mcp-config` + mock server，工具出现在可用列表 |
| `mcp.stdio_tool_call` | 模型 fixture 调 `mcp__echo__echo`，结果序列化 |
| `mcp.env_expansion` | config 中 `${ENV_VAR}` 展开 |

**轴 5：CLI 输出补漏**：非零退出码矩阵、stderr/stdout 边界、`--output-format stream-json` + `--include-partial-messages`、stream-json 输入侧（`--input-format stream-json`）。

**轴 6：compact / auto-compact**：先做调查任务——确认 print mode 下 compact 的可触发方式（`/compact` 提示词、token 阈值 fixture）；结论写进 case 或标记 `wont-align`（若仅交互态可触发，降级为单测 + request-side 检查）。

**轴 7：subagent / Agent**：fixture 驱动 Agent tool 调用（嵌套 query loop 需要 fixture 支持多会话应答序列；如 mock server 需扩展按 system prompt 区分主/子会话，先做 runner 调查再定 case）。

### 每个 case 的落地格式

- `hare/alignment/cases/<group>/<name>/case.json`（`policy.golden_source` 必须 `"ts-reference"`）
- `hare/alignment/fixtures/<name>.json`
- `hare/alignment/golden/<group>/<name>/golden.json`
- 文件系统输入放 `hare/alignment/seeds/`

### 录制与验证流程（每个 case 固定五步）

```bash
# 1. 写 case.json + fixture
# 2. 用 TS reference 录 golden
CLAUDE_TS_CLI="bun $PWD/recovered-from-cli-js-map/src/entrypoints/cli.tsx --no-chrome" \
  python scripts/record_golden.py <case_id>
# 3. 检查 golden 合理性（人工读一遍，确认没把 TS 的 bug 或环境噪声录进去）
# 4. 跑 Python 比对
python -m pytest tests/e2e -k <case_id> -q
# 5. 通过 → 提交；失败 → 差异记入阶段 5 待修清单（或 known_divergence + 注明原因）
```

### 完成标准

- 每条 P0/P1 轴至少 2 个 golden E2E case，parity matrix 对应项从 `implemented-unverified` 翻成 `aligned` 或暴露成待修差异。
- 新 case 不依赖网络、不写真实用户目录、不读取真实 `~/.hare/` 或 `~/.claude/`。
- 差异只通过 `known_divergence`（写明原因和后续动作）或修实现处理，不扩大 normalizer。

## 阶段 5：按模块清偿行为差异（主线，与阶段 4 交替）

### 目标

把差分 case 暴露出来的差异转化为可追踪的小修复，而不是大范围重写。

### 工作方式（每个差异一个循环）

1. 选一个失败的 golden case（或 `known_divergence` 待修项）。
2. 读 TS 源确认参考行为（`recovered-from-cli-js-map/src/...`），在修复 commit message 里引用 TS 文件路径。
3. 最小范围修改 Python 实现。
4. **处理存量测试冲突（原则 4）**：修复导致存量单测失败时，逐个判断——测试锁的是 JS 对齐行为则说明修复错了；锁的是 Python 旧现状（尤其 `coverage_*` / `branch_*` / `hit_80` 类）则直接改测试断言，并在 commit 里注明"updated to match ts-reference behavior"。
5. 增加或更新 targeted unit test（断言新行为）。
6. 复跑该轴 E2E 和相关单测。
7. 更新 parity matrix 对应项为 `aligned`，移除 `known_divergence`。

### 推荐顺序

1. `session persistence`（阶段 4 轴 1 的产出）
2. `permissions`
3. `CLI output`
4. `tools`
5. `hooks`
6. `MCP`
7. `compact`
8. `subagent / Agent`

### 每次修复的验收

```bash
python -m pytest tests/e2e -q
make alignment-guardrails
python scripts/check_mypy_regression.py --baseline 520
python -m pytest hare/tests/test_hare_<module>*.py -q   # 视模块
```

### 完成标准

- 修复能被一个或多个 golden E2E case 证明。
- 不扩大 normalizer；不新增 mypy 错误；不新增 `NotImplementedError`。
- 每个提交一个主题：一个差异 = 一个提交（实现 + 测试 + matrix 更新）。

## 阶段 6：Dogfood —— 作为产品验证（每完成 1-2 条轴跑一轮）

### 目标

golden diff 证明的是"和 TS 一样"，dogfood 证明的是"作为 code agent 能用"。两者互补，后者会暴露合成 case 想不到的问题。

### 任务

1. 建立固定 dogfood 清单 `docs/alignment-status/dogfood-checklist.md`，至少包含：
   - 在一个临时 git 仓库里，用 `python -m hare -p "..."` 完成一次真实文件修改任务（Read → Edit → 验证）。
   - 一次多轮任务：第一轮建文件，`--resume` 第二轮修改它。
   - 一次带 deny 规则的任务，确认拦截提示对用户可理解。
   - 一次接 mock/真实 MCP server 的工具调用。
   - （有条件时）`make test-live` 打真模型的 smoke。
2. 每轮 dogfood 的失败或体验问题，登记为新的 alignment case（回流阶段 4）或 issue，不允许只留在记忆里。

### 完成标准

- 每完成 1-2 条轴，dogfood 清单全绿一次并在状态快照里记录日期和结果。
- 每个 dogfood 失败项都有对应 case 或待办条目。

## 阶段 7：mypy 类型债 —— 只保门，不主动清偿

### 目标

保持回归门有效即可，主动压基线的工作排在功能对齐基本完成之后。

### 任务

1. 每次提交前跑 `python scripts/check_mypy_regression.py --baseline 520`，不新增错误。
2. 基线只允许向下改：若某次行为修复顺带消掉若干 mypy 错误，随手把 baseline 降到新值。
3. **不**安排专门的按模块清型债任务，直到 parity matrix 的 P0/P1 项全部 `aligned` 或 `wont-align`。

### 完成标准

- `make mypy-regression` 持续通过，baseline 单调不升。

## 阶段 8：收口与发布前检查

### 最终检查清单

```bash
git status --short --branch
make alignment-guardrails
python -m pytest tests/e2e -q
python scripts/verify_alignment.py
python scripts/gen_parity_matrix.py --check
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

- 工作树只包含当前阶段相关改动；每个提交只包含一个清晰主题。
- parity matrix 与状态文档反映真实现状。
- 推送前没有 `node_modules/`、缓存、coverage、临时文件进入 git。

## 建议执行顺序

1. **阶段 0 → 1 → 2 一口气过掉**（合计约 1.5 天）：垃圾清理、路径语义、守卫。这是一次性地基。
2. **阶段 3 建 parity matrix**（2-3 天）：先有地图再行军。
3. **阶段 4.0 基础设施前置**（约 1 天）：TS reference 跑通 + runner 多调用扩展。这是后面一切录制的前提，**如果 TS reference 在本机跑不通，这是最高优先级阻塞**。
4. **阶段 4 / 5 交替作为长期主线**：一条轴补 case → 修差异 → matrix 翻绿 → 下一条轴。从 session、permission 开始。
5. **每完成 1-2 条轴，跑一轮阶段 6 dogfood**。
6. **阶段 7 全程只保门**；阶段 8 在每次准备推送/发 PR 时执行。

## 近期第一批任务（可直接开工）

1. 执行阶段 0 全部任务（垃圾目录、egg-info、状态快照）。
2. 补齐阶段 2 的三个新守卫并接入 `alignment-guardrails`。
3. 验证 TS reference：`cd recovered-from-cli-js-map && bun install && bun src/entrypoints/cli.tsx --no-chrome --version`。跑不通就先修/记录阻塞。
4. 写 `scripts/gen_parity_matrix.py` 第一版（先只提取 CLI flags 和 tools 两个维度，跑通闭环再扩）。
5. 扩展 `scripts/e2e_runner.py` + `scripts/record_golden.py` 支持 `invocations` 多次调用与 `${session_id[N]}` 注入，带单测。
6. 录制 `session.transcript_write`、`session.resume_basic`、`session.continue_basic` 三个 golden，跑出第一批真差异。
7. 对暴露的差异按阶段 5 流程做前 1-2 个小修复，验证"case → 修复 → matrix 翻绿"整个循环成立。

## 暂停点

遇到下面情况时先停下来复盘，不要继续堆改动：

- 需要改 normalizer 才能让 case 通过。
- 新 case 会访问真实网络或真实用户目录。
- TS reference 在本机无法运行，导致只能"凭记忆"写 golden —— 禁止，必须先解决运行问题。
- golden 里录进了明显的 TS 侧 bug 或环境噪声，拿不准该对齐还是标 `known_divergence`。
- `legacy_alignment` 与 `hare/alignment` 对同一行为给出相反结论。
- 一次行为修复导致超过 ~20 个存量单测失败 —— 先确认不是修错了方向，再批量改测试。
- `mypy` baseline 需要上调。
- 新增对齐资产必须复制到两个目录才能跑通。
