# Hare 端到端(E2E)差分测试手册

这套 E2E 把 hare 的真实 CLI 行为和 **TS 原版 Claude Code** 做差分对齐,作为"正确性"的 oracle。全部自包含在 `hare/` 下。

## 架构一句话

一份 **fixture**(描述模型该怎么回复)同时喂给两边:hare 走 **Layer A**(进程内注入),TS 原版走 **Layer B**(本地 mock HTTP server)。两边输出经同一 normalizer 抹掉不确定性后逐字节对比。

```
                 ┌─ Layer A: HARE_MODEL_FIXTURE ──► hare (python -m hare) ─► 实际输出
fixture.json ──┤                                                              ├─► 对比
                 └─ Layer B: mock SSE server ─────► TS 原版 (claude) ───────► golden
```

## ⚠️ 必读约束

- **hare 的模型客户端用 OAuth 凭证打真实 API,且忽略 `ANTHROPIC_BASE_URL`。** 所以 hare **只能**用 Layer A(fixture 注入)。**不带 fixture 跑模型相关的 hare case = 真实、不确定、计费的 API 调用。** E2E 测试已强制:每个 case 要么声明 `fixture`,要么标 `kind:"deterministic"`(纯 CLI、不到模型,如 `--version`)。
- 本机连 localhost 必须绕代理:脚本已自动设 `NO_PROXY=127.0.0.1,localhost`。
- golden 是事实基线,入库 git 跟踪。改了 fixture 或升级 TS 原版后需重录。

## 目录约定(均在 `hare/` 下)

| 路径 | 内容 |
|---|---|
| `hare/alignment/fixtures/<name>.json` | 模型行为 fixture(scripted 手写 / replay 录制) |
| `hare/alignment/seeds/<path>` | case 需要的种子文件(会拷进沙箱 cwd) |
| `hare/alignment/cases/<group>/<name>/case.json` | case 定义 |
| `hare/alignment/golden/<group>/<name>/golden.json` | 从 TS 录出的期望输出 |
| `scripts/e2e_runner.py` | 跑 hare CLI 子进程(Layer A) |
| `scripts/mock_anthropic_server.py` | mock SSE server(Layer B,喂 TS) |
| `scripts/record_golden.py` | 驱动 TS 录 golden |
| `tests/e2e/test_e2e_cases.py` | golden 比对入口 |

## fixture 格式

```json
{
  "kind": "scripted",
  "responses": [
    {
      "stop_reason": "tool_use",
      "content": [
        {"type": "text", "text": "我读一下 README。"},
        {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "README.md"}}
      ],
      "usage": {"input_tokens": 50, "output_tokens": 20}
    },
    {
      "stop_reason": "end_turn",
      "content": [{"type": "text", "text": "这是个 Python 项目。"}],
      "usage": {"input_tokens": 120, "output_tokens": 10}
    }
  ]
}
```

第 N 次模型调用取 `responses[N]`。`tool_use` 后 hare 执行工具、喂回结果,再取下一条。

## 配置 TS 原版入口

```bash
export CLAUDE_TS_CLI="claude"   # 官方 CLI(实测可 headless 跑)
```

**实测结论(2026-06-13):**
- **官方 `claude` CLI 可用**:headless `--print` 正常,认 `ANTHROPIC_BASE_URL` + api-key → 命中 mock,正确解析 SSE。`record_golden.py` 会自动清掉 `ANTHROPIC_AUTH_TOKEN`、用 dummy api-key、临时 `CLAUDE_CONFIG_DIR`,把官方 CLI 强制导向 mock。
- **recovered-from-cli-js-map 的 TS CLI 不可用**:headless `-p` 模式在 query/print 路径**卡死**(init 正常,之后静默挂起,从不发 API 请求)。要用它需先修那份 recovered 代码的 print 路径。
- **版本警告**:本机官方 `claude` 是 **2.1.165**,hare 复现 **2.1.88**。两者差分里的差异**可能是版本漂移**,需甄别。

### 已知分歧(known_divergence)

差分发现的真实差异**不要**靠改 golden 抹掉。在 `case.json` 加 `"known_divergence": "<原因>"`:该 case 在 stdout 不一致时记为 **xfail**(套件保持绿、可追踪),一旦 hare 修好会变 **xpass** 告警。当前已记录一例:`chat.single_turn` —— hare 在 print 模式多输出一个开头换行(见 `main.py` print 路径)。

## 加一个 case 的四步

### A. 确定性 CLI case(不到模型,如 `--version`/`--help`)

1. 写 `hare/alignment/cases/<g>/<n>/case.json`,加 `"kind": "deterministic"`。
2. 跑一次 hare 看输出:`python scripts/e2e_runner.py hare/alignment/cases/<g>/<n>/case.json`。
3. 人工核对后把输出冻进 `hare/alignment/golden/<g>/<n>/golden.json`(至少 `case_id`/`status`/`state.exit_code`/`stdout`)。
4. `pytest tests/e2e -k <case_id>` 跑绿。

### B. 模型驱动 case(单轮问答 / 工具调用 / 权限 / 压缩)

1. 写 `hare/alignment/fixtures/<name>.json`。
2. 写 `case.json`，并使用 `"fixture": "hare/alignment/fixtures/<name>.json"`。旧的 `"alignment/fixtures/..."` 已不再支持。需要文件的加 `"fs": {"seed": [...]}`。
3. 录 golden(需 TS 原版):`python scripts/record_golden.py <case_id>`。
4. `pytest tests/e2e -k <case_id>` 跑绿。若 hare 与 TS 输出有差异,这正是要发现的对齐 bug——记录下来,**不要**为了过测在 normalize 里抹掉真实差异。

## Live 测试(真实模型后端)

差分套件用 fixture 固定模型输出(确定性);它**证明不了** hare 能和真实模型完成真实对话。`tests/live/` 是 opt-in 的真实端到端冒烟:

```bash
make test-live        # 需要可用的 ANTHROPIC_* 后端(如 ~/.claude.json 里的 DeepSeek);会消耗 token
# 等价:HARE_LIVE_TESTS=1 python -m pytest tests/live/
```

**CI**:`.github/workflows/live-smoke.yml` 手动触发(Actions 页面)+ 每周一 06:00 UTC 定时跑。用 repo secrets(`ANTHROPIC_AUTH_TOKEN` 或 `ANTHROPIC_API_KEY`,可选 `ANTHROPIC_BASE_URL`/`ANTHROPIC_MODEL`);未配置 secrets 时该 job no-op(不报错)。不进 per-PR 流水线(计费)。

默认(无 `HARE_LIVE_TESTS`)本地自动跳过,不进常规/CI 计费。覆盖:
- 基本问答(真实 HTTP + 流式 + 系统提示路径)
- **真实 agentic 工具循环**:模型自行决定调 Read 工具读临时文件、再据内容回答(`num_turns≥2`,结果含文件里的随机 marker)——验证工具广告(注册表)+ 工具使用指南(系统提示)+ 多轮循环在真实模型下协同工作。断言是宽松的属性检查(非逐字)。

## 常用命令

```bash
make e2e                          # 跑所有确定性 E2E(Layer A,无网络)
python scripts/record_golden.py <case_id>   # 重录某 case 的 TS golden
python scripts/mock_anthropic_server.py hare/alignment/fixtures/<name>.json 8089  # 手动起 mock
```

## 本 harness 无法干净差分的面(已评估,2026-06-14)

差分靠 fixture 固定模型**输出**,所以只能差分 hare 对固定输出的**处理**。以下不在范围内:
- **`--system-prompt` / `--append-system-prompt`**:只影响模型**输入**;fixture 已固定输出,两边输出逐字节相同,测了无意义(已验证)。
- **`--continue` / `--resume`(跨调用会话续接)**:需要持久化的会话状态,且 hare 与 Claude Code 的会话 JSONL 格式本就不同,不是可比对象。**一次调用内的多轮 agentic loop**(模型多次被调)是真正有价值的多轮,已覆盖(`tools.multi_turn_three`、`json.long_tool_chain` 5 轮)。
- **网络工具(WebFetch/WebSearch)**:真实执行会打网络、不确定;跳过。
- **`Task` 子代理工具**:会触发额外模型调用,fixture 难以确定性驱动;跳过。
- **`stop_reason: max_tokens`**:Claude Code 会**自动续写**(发第 2 次请求),需多响应 fixture 才能测;单响应会让参考 CLI 卡住,故不收录。

## golden 何时需要重录

- 改了对应 fixture。
- 升级了 TS 原版版本(行为可能变)。
- normalizer 规则变化导致归一化结果变化。
