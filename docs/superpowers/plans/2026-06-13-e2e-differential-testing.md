# Hare 端到端差分测试框架 Implementation Plan

> Historical planning note (updated 2026-07-06): this file documents the 2026-06-13 implementation plan and its then-current assumptions.
> It should not be used as the current status source.
> For current verified state and directory ownership, use `REVIEW_2026-07-02.md`, `docs/alignment-status/2026-07-07.md`, and `docs/e2e-testing.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 hare 建一套确定性的端到端测试框架——用同一份"模型行为 fixture",既能让 hare 自我防回归(快、无网络),又能把 hare 的真实输出和 TS 原版 Claude Code 的输出做差分对齐(真·正确性 oracle)。

**Architecture:** 三个支柱共享一份 fixture 格式和一个 normalizer。
(1) **Layer A — 进程内假模型**:`production_deps()` 读环境变量 `HARE_MODEL_FIXTURE`,把 `call_model` 换成按 fixture 回放的假模型。`python -m hare` 子进程因此完全确定性、不打网络。
(2) **Layer B — mock Anthropic HTTP server**:把同一份 fixture 转成 SSE 流通过 `/v1/messages` 提供;hare 和 TS 原版都用 `ANTHROPIC_BASE_URL` 指向它,于是能跑真实 HTTP 客户端路径并做差分。
(3) **Normalizer + Runner + Comparator**:剥离时间戳/uuid/耗时/绝对路径等不确定性后,把 hare 实际输出与 golden 逐 case 比对,按优先级权重打分。golden 由 TS 原版经 Layer B 录出。

**Tech Stack:** Python 3.12, pytest + pytest-asyncio, `anthropic` SDK (hare 已用 `AsyncAnthropic`,认 `ANTHROPIC_BASE_URL`), 标准库 `http.server` 做 mock server, Node/npm 跑 TS 原版录 golden。复用既有 `scripts/alignment_runner.py`、`scripts/compare_alignment.py`、`scripts/alignment_mocks.py`。

---

## ⚠️ 执行期修正(2026-06-13,棕地现实; 历史记录)

执行中发现计划原前提多处与现实不符,已与用户对齐方向。**真实情况:**
- git 仓库根是**外层** `claude-code-recover-and-python-reset`;`hare/` 是进行中的迁移目标子目录。
- 外层 `alignment/` 已有 **519 个 case** + `normalize.py` + schema + runner + comparator,且 `tests/alignment/` **1558 passed**——框架是活的,**不是空壳**(原计划"cases 全空、normalize 缺失"判断错误,看错成了 `hare/alignment`)。
- 但现有 1558 个测试**只断言 case 能跑 + 输出形状/schema 合法,不比对 TS 参考**;真正做差分的 `compare_alignment.py` 在 Makefile 里**全部以 `--ts /dev/null --py-only` 调用**——**TS 差分被 stub 掉了,没有任何东西验证 hare 与真实 Claude Code 一致**。这正是真实缺口。
- 现有 519 case 绝大多数是**模块级**(`entrypoint.kind: module`);**CLI 子进程级 E2E 几乎空白**。

**用户拍板的两个方向(覆盖原计划相应部分):**
1. **聚焦"补真·TS 差分"**:把 `--ts /dev/null` 换成对真实 TS 原版录出的 golden(mock-server 方案不变)。
2. **全部收敛进 `hare/`**:新 E2E 框架/case/golden/fixture 全放 `hare/` 下、git 跟踪、自包含;**不复用也不修改**外层那套(及 `alignment_runner.py` 的外层 `PROJECT_ROOT`),改为新建一个**根在 `hare/`** 的自包含 E2E runner(`hare/scripts/e2e_runner.py`),以免弄坏现有绿色套件。Task 5「修改 alignment_runner」因此改为「新建 e2e_runner」;其余 Task 的路径根由"外层"改为 `hare/`。

下文 Phase 0 已完成(Layer A,已提交)。Phase 1 起按上述修正执行。

---

## 背景:当前真实现状(已核实)

- `production_deps()`(`hare/query/deps.py:53-59`)是模型注入的唯一咽喉点;`hare/query/core.py:186` 是 `deps = params.deps or production_deps()`,改 `production_deps()` 即可注入整个 CLI 路径,其余零改动。
- `query_model_with_streaming(payload)`(`hare/services/api/claude.py:1432`)是生产 `call_model`;契约是"接收单个 `payload` dict,返回可被 `_iter_call_model_result` 迭代的流"(见 `core.py:1129/1146`)。`scripts/alignment_mocks.py` 的 `scripted_model_factory` 已实现该契约:yield `StreamEvent(event={"content":[...], "stop_reason":..., "usage":{...}})`。
- hare 的 HTTP 客户端 `hare/services/api/client.py:137,161,169` 读 `ANTHROPIC_BASE_URL` 并构造 `anthropic.AsyncAnthropic(base_url=...)` → Layer B 可行。
- `scripts/alignment_runner.py` 的 `run_case_cli()` 已经会 `subprocess.run([sys.executable, "-m", "hare", *argv])` 并按 `stdout_kind` 解析 ndjson。**缺**:不设确定性环境变量、不做 fs 沙箱、`files` 永远返回 `[]`。
- `scripts/compare_alignment.py` 顶部 `from normalize import normalize_result`,但 **`alignment/normalize.py` 不存在**——现在一跑就 ImportError。
- `alignment/cases/{P1,P2}` 为空,`*.json` 数量为 0。alignment 框架是"半成品脚手架",从未真正跑通。
- `tests/` 有 ~16 个 coverage-chasing 文件(`test_hit_80`、`test_coverage_boost`、`test_branch_gap_close` 等),制造虚假安全感,Phase 4 清理。

---

## File Structure

**新建:**
- `alignment/fixtures/` — 共享 fixture 目录(模型行为)。
- `alignment/fixtures/_schema.md` — fixture 格式说明。
- `hare/testing/__init__.py`、`hare/testing/fake_model.py` — 进程内假模型工厂(Layer A),包内可被子进程 import。
- `alignment/normalize.py` — normalizer(被 `compare_alignment.py` 依赖,当前缺失)。
- `scripts/mock_anthropic_server.py` — mock Anthropic SSE server(Layer B)。
- `scripts/record_golden.py` — 驱动 TS 原版经 Layer B 录 golden。
- `alignment/cases/**/case.json` — 各场景 case(首批 4 组)。
- `alignment/golden/**/golden.json` — TS 录出的归一化期望输出。
- `tests/e2e/test_e2e_cases.py` — pytest 入口,跑 Layer A 子进程并与 golden 比对。
- `docs/e2e-testing.md` — 给人看的"怎么加 case / 怎么重录 golden"操作手册。

**修改:**
- `hare/query/deps.py:53-59` — `production_deps()` 接入 `HARE_MODEL_FIXTURE`。
- `scripts/alignment_runner.py:74-110` — `run_case_cli()` 设确定性 env + fs 沙箱 + 文件快照。
- `Makefile` — 新增 `e2e`、`e2e-record`、`mock-server` 目标。
- `.github/workflows/*.yml` — CI 跑 Layer A E2E(确定性、无网络);Layer B 重录设为手动/定时。

---

## 设计要点:统一的 fixture 格式

一份 fixture = 一次会话里模型按顺序产生的若干次 response。每次 response 同时够 Layer A(直接 yield)和 Layer B(转成 SSE)使用。

```json
{
  "kind": "scripted",
  "responses": [
    {
      "stop_reason": "tool_use",
      "content": [
        {"type": "text", "text": "我来看一下这个文件。"},
        {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "README.md"}}
      ],
      "usage": {"input_tokens": 50, "output_tokens": 20}
    },
    {
      "stop_reason": "end_turn",
      "content": [{"type": "text", "text": "这是一个 Python 项目。"}],
      "usage": {"input_tokens": 80, "output_tokens": 12}
    }
  ]
}
```

- `kind: "scripted"` = 你手写;`kind: "replay"` = 录制代理从真实 API 抓的(同格式,见 Phase 2)。
- 第 N 次 `call_model` 取 `responses[N]`。`tool_use` response 后,hare 执行工具、把结果喂回,再取下一条 response。

> **已核实的实现约束(2026-06-13,执行时确认):** `hare/query/core.py` 的 `_stream_model_turn` 直接 `yield _coerce_query_yield(item)`,**没有** StreamEvent→AssistantMessage 装配层;而工具循环只对 `AssistantMessage` 提取 `tool_use`(core.py:383)。因此假模型必须 yield `{"type":"assistant", "content":[...], "stop_reason":..., "usage":...}` 的 **dict**(`_coerce_query_yield` 会转成 `AssistantMessage`),**不能** yield `StreamEvent`——否则工具永不执行。`scripts/alignment_mocks.py:scripted_model_factory` 用 StreamEvent 仅适合无工具的单轮显示,不可照搬到工具场景。

---

## Phase 0 — 确定性地基(Layer A)

### Task 1: 包内假模型工厂 `hare/testing/fake_model.py`

**Files:**
- Create: `hare/testing/__init__.py`
- Create: `hare/testing/fake_model.py`
- Test: `tests/test_fake_model.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_fake_model.py
import json
import pytest
from hare.testing.fake_model import load_fixture, fixture_call_model


@pytest.mark.asyncio
async def test_fixture_call_model_yields_scripted_responses_in_order(tmp_path):
    fixture_path = tmp_path / "fx.json"
    fixture_path.write_text(json.dumps({
        "kind": "scripted",
        "responses": [
            {"stop_reason": "tool_use",
             "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
             "usage": {"input_tokens": 1, "output_tokens": 1}},
            {"stop_reason": "end_turn",
             "content": [{"type": "text", "text": "done"}],
             "usage": {"input_tokens": 1, "output_tokens": 1}},
        ],
    }), encoding="utf-8")

    fixture = load_fixture(fixture_path)
    call_model = fixture_call_model(fixture)

    # 第一次调用 -> 第一条 response(assistant dict,_coerce_query_yield 会转成 AssistantMessage)
    msgs = [m async for m in _aiter(call_model({"messages": []}))]
    assert msgs[-1]["type"] == "assistant"
    assert msgs[-1]["stop_reason"] == "tool_use"
    # 第二次调用 -> 第二条 response
    msgs = [m async for m in _aiter(call_model({"messages": []}))]
    assert msgs[-1]["stop_reason"] == "end_turn"
    assert msgs[-1]["content"][0]["text"] == "done"


async def _aiter(value):
    # call_model 可能返回 async-gen 或 coroutine-of-async-gen,统一展开
    if hasattr(value, "__aiter__"):
        async for x in value:
            yield x
        return
    inner = await value
    async for x in inner:
        yield x
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_fake_model.py -v`
Expected: FAIL — `ModuleNotFoundError: hare.testing.fake_model`

- [ ] **Step 3: 实现**

```python
# hare/testing/__init__.py
"""Test-only helpers that ship inside the package so subprocess CLI runs
(``python -m hare``) can import them. Not used on the production code path."""
```

```python
# hare/testing/fake_model.py
"""Deterministic fake model backed by a fixture file.

Mirrors the contract of ``hare.services.api.claude.query_model_with_streaming``:
a callable taking a single ``payload`` dict and returning an async iterator of
``StreamEvent``. Each successive call consumes the next fixture response, so a
single fixture drives a whole multi-turn session deterministically.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncGenerator, Callable


def load_fixture(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("kind") not in {"scripted", "replay"}:
        raise ValueError(f"fixture kind must be scripted|replay, got {data.get('kind')!r}")
    if not isinstance(data.get("responses"), list):
        raise ValueError("fixture must have a 'responses' list")
    return data


def fixture_call_model(fixture: dict[str, Any]) -> Callable[..., AsyncGenerator[Any, None]]:
    """Return a stateful ``call_model`` that yields the next response per call."""
    responses = list(fixture["responses"])
    index = {"i": 0}

    def call_model(*_args: Any, **_kwargs: Any) -> AsyncGenerator[Any, None]:
        async def _gen() -> AsyncGenerator[Any, None]:
            i = index["i"]
            if i >= len(responses):
                raise AssertionError(
                    f"fixture exhausted: model called {i + 1} times but fixture "
                    f"only has {len(responses)} responses"
                )
            index["i"] = i + 1
            r = responses[i]
            # assistant dict — _coerce_query_yield 转成 AssistantMessage 驱动工具循环
            yield {
                "type": "assistant",
                "content": r.get("content", [{"type": "text", "text": ""}]),
                "stop_reason": r.get("stop_reason", "end_turn"),
                "usage": r.get("usage", {"input_tokens": 0, "output_tokens": 0}),
            }

        return _gen()

    return call_model
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_fake_model.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add hare/testing/__init__.py hare/testing/fake_model.py tests/test_fake_model.py
git commit -m "test: add fixture-backed fake model for deterministic E2E (Layer A)"
```

---

### Task 2: 把假模型接进 `production_deps()`

**Files:**
- Modify: `hare/query/deps.py:53-59`
- Test: `tests/test_deps_fixture_injection.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_deps_fixture_injection.py
import json
from hare.query.deps import production_deps
from hare.services.api.claude import query_model_with_streaming


def test_production_deps_uses_real_model_without_env(monkeypatch):
    monkeypatch.delenv("HARE_MODEL_FIXTURE", raising=False)
    deps = production_deps()
    assert deps.call_model is query_model_with_streaming


def test_production_deps_uses_fixture_when_env_set(monkeypatch, tmp_path):
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({
        "kind": "scripted",
        "responses": [{"stop_reason": "end_turn",
                       "content": [{"type": "text", "text": "hi"}],
                       "usage": {"input_tokens": 1, "output_tokens": 1}}],
    }), encoding="utf-8")
    monkeypatch.setenv("HARE_MODEL_FIXTURE", str(fx))
    deps = production_deps()
    assert deps.call_model is not query_model_with_streaming
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_deps_fixture_injection.py -v`
Expected: FAIL — 第二个用例失败(env 设了但 call_model 仍是生产函数)

- [ ] **Step 3: 实现**(改 `hare/query/deps.py` 的 `production_deps`)

```python
def production_deps() -> QueryDeps:
    import os

    call_model = query_model_with_streaming
    fixture_path = os.environ.get("HARE_MODEL_FIXTURE")
    if fixture_path:
        # Test-only deterministic backend. Never set in production.
        from hare.testing.fake_model import fixture_call_model, load_fixture

        call_model = fixture_call_model(load_fixture(fixture_path))

    return QueryDeps(
        call_model=call_model,
        microcompact=microcompact_messages,
        autocompact=auto_compact_if_needed,
        uuid=lambda: str(_uuid.uuid4()),
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_deps_fixture_injection.py tests/test_fake_model.py -v`
Expected: PASS（全部）

- [ ] **Step 5: 提交**

```bash
git add hare/query/deps.py tests/test_deps_fixture_injection.py
git commit -m "feat: inject fixture-backed model via HARE_MODEL_FIXTURE env"
```

---

### Task 3: 全链路冒烟——`python -m hare` 子进程确定性输出

**Files:**
- Create: `alignment/fixtures/single_turn_hello.json`
- Test: `tests/e2e/__init__.py`, `tests/e2e/test_smoke_subprocess.py`

- [ ] **Step 1: 写 fixture + 失败测试**

```json
// alignment/fixtures/single_turn_hello.json
{
  "kind": "scripted",
  "responses": [
    {"stop_reason": "end_turn",
     "content": [{"type": "text", "text": "Hello from fixture."}],
     "usage": {"input_tokens": 5, "output_tokens": 4}}
  ]
}
```

```python
# tests/e2e/test_smoke_subprocess.py
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "alignment" / "fixtures" / "single_turn_hello.json"


def test_print_mode_uses_fixture_and_is_deterministic():
    env = dict(os.environ)
    env["HARE_MODEL_FIXTURE"] = str(FIXTURE)
    env["ANTHROPIC_API_KEY"] = "test-key-not-used"
    # 跑两次,输出必须逐字节一致,且包含 fixture 文本
    outs = []
    for _ in range(2):
        proc = subprocess.run(
            [sys.executable, "-m", "hare", "-p", "say hi"],
            capture_output=True, text=True, timeout=60, env=env, cwd=str(REPO / "hare"),
        )
        assert proc.returncode == 0, proc.stderr
        outs.append(proc.stdout)
    assert "Hello from fixture." in outs[0]
    assert outs[0] == outs[1], "subprocess output not deterministic"
```

- [ ] **Step 2: 跑测试**

Run: `pytest tests/e2e/test_smoke_subprocess.py -v`
Expected: 先确认 `-p`(print 模式)是 hare 真实参数;若不是,用 `grep -n "add_argument" hare/main.py` 找到等价的非交互/print 参数后替换 `argv`。修正后 Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add alignment/fixtures/single_turn_hello.json tests/e2e/__init__.py tests/e2e/test_smoke_subprocess.py
git commit -m "test: E2E smoke — CLI subprocess deterministic via fixture"
```

> **Phase 0 出口标准:** `python -m hare` 子进程在 fixture 下完全确定性、不打网络。这是后面一切的地基。

---

## Phase 1 — Normalizer + E2E 比对管线

### Task 4: 补齐缺失的 `alignment/normalize.py`

`compare_alignment.py` 依赖它但文件不存在。normalizer 负责把两边输出里的不确定性抹平,否则 golden 永远对不上。

**Files:**
- Create: `alignment/normalize.py`
- Test: `tests/test_normalize.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_normalize.py
from alignment.normalize import normalize_result


def test_normalize_strips_nondeterminism():
    raw = {
        "case_id": "x", "priority": "P1", "status": "ok",
        "stdout": "session 4f0c-uuid done in 1234ms cost $0.000123 at /tmp/abc/file",
        "events": [{"session_id": "abc-123", "duration_ms": 42, "uuid": "deadbeef"}],
        "state": {"exit_code": 0},
        "duration_ms": 999,
    }
    out = normalize_result(raw, sandbox_root="/tmp/abc")
    # uuid / 毫秒 / cost / 沙箱绝对路径 都被占位符替换
    assert "4f0c-uuid" not in out["stdout"]
    assert "1234ms" not in out["stdout"]
    assert "/tmp/abc" not in out["stdout"]
    assert out["events"][0]["session_id"] == "<UUID>"
    assert out["events"][0]["duration_ms"] == "<DURATION>"
    # duration_ms 顶层字段被丢弃(纯计时,不参与比对)
    assert "duration_ms" not in out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_normalize.py -v`
Expected: FAIL — `ModuleNotFoundError: alignment.normalize`

- [ ] **Step 3: 实现**

```python
# alignment/normalize.py
"""Strip nondeterminism from runner output before golden comparison.

Both the recorded golden (from the TS reference) and hare's live output are
passed through this same function, so timestamps/uuids/paths/cost-jitter never
cause spurious diffs. Keep this conservative: only mask things that are
*provably* nondeterministic — masking real behavior hides bugs."""

from __future__ import annotations

import re
from typing import Any

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}")
_MS_RE = re.compile(r"\b\d+(?:\.\d+)?ms\b")
_COST_RE = re.compile(r"\$\d+\.\d+")
_VOLATILE_KEYS = {"session_id", "uuid", "request_id", "message_id"}
_DURATION_KEYS = {"duration_ms", "duration_api_ms", "ttft_ms", "created_at", "timestamp"}
_DROP_TOPLEVEL = {"duration_ms"}


def _scrub_str(s: str, sandbox_root: str | None) -> str:
    if sandbox_root:
        s = s.replace(sandbox_root, "<SANDBOX>")
    s = _UUID_RE.sub("<UUID>", s)
    s = _MS_RE.sub("<DURATION>", s)
    s = _COST_RE.sub("<COST>", s)
    return s


def _scrub(obj: Any, sandbox_root: str | None) -> Any:
    if isinstance(obj, str):
        return _scrub_str(obj, sandbox_root)
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in _VOLATILE_KEYS:
                out[k] = "<UUID>"
            elif k in _DURATION_KEYS:
                out[k] = "<DURATION>"
            else:
                out[k] = _scrub(v, sandbox_root)
        return out
    if isinstance(obj, list):
        return [_scrub(x, sandbox_root) for x in obj]
    return obj


def normalize_result(result: dict[str, Any], sandbox_root: str | None = None) -> dict[str, Any]:
    out = {k: v for k, v in result.items() if k not in _DROP_TOPLEVEL}
    return _scrub(out, sandbox_root)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_normalize.py -v && python -c "import sys; sys.path.insert(0,'scripts'); import compare_alignment"`
Expected: PASS,且 `compare_alignment` 现在能 import 成功(之前 ImportError)

- [ ] **Step 5: 提交**

```bash
git add alignment/normalize.py tests/test_normalize.py
git commit -m "fix: add missing alignment/normalize.py (compare_alignment was broken)"
```

---

### Task 5: runner 加确定性 env + fs 沙箱 + 文件快照

**Files:**
- Modify: `scripts/alignment_runner.py`（`_prepare_env`、`run_case_cli`）
- Test: `tests/test_runner_sandbox.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_runner_sandbox.py
import sys
sys.path.insert(0, "scripts")
from alignment_runner import run_case_cli


def test_runner_injects_fixture_and_snapshots_files(tmp_path):
    case = {
        "case_id": "smoke.write",
        "priority": "P1",
        "entrypoint": {"argv": ["-p", "hi"]},
        "fixture": "alignment/fixtures/single_turn_hello.json",
        "expected": {"exit_code": 0, "stdout_kind": "text"},
        "policy": {},
    }
    result = run_case_cli(case)
    assert result["status"] == "ok"
    # files 字段不再恒为空——它是 (相对路径, sha256/内容) 的快照列表
    assert isinstance(result["files"], list)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_runner_sandbox.py -v`
Expected: FAIL — runner 没读 `case["fixture"]`,且子进程缺 `ANTHROPIC_API_KEY` 会报错 / `files` 行为未定义

- [ ] **Step 3: 实现**——在 `scripts/alignment_runner.py` 改两处:

`_prepare_env(case)` 末尾、`return env` 之前加入:

```python
    # 确定性后端:把 case.fixture 转成绝对路径喂给子进程的 Layer A 假模型
    fixture = case.get("fixture")
    if fixture:
        env["HARE_MODEL_FIXTURE"] = str((PROJECT_ROOT / fixture).resolve())
    # 子进程构造 AsyncAnthropic 需要 key 存在(假模型不会真用它)
    env.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
    # 抹掉会引入不确定性的环境
    env["HARE_DISABLE_TELEMETRY"] = "1"
    env["TERM"] = "dumb"
```

`run_case_cli` 里把固定的 `cwd = _prepare_cwd(case)` 换成"沙箱拷贝",并在跑完后快照文件:

```python
    import hashlib, shutil, tempfile
    sandbox = Path(tempfile.mkdtemp(prefix="hare-e2e-"))
    src = _prepare_cwd(case)
    # 只拷贝 case 声明的 seed 文件,保持沙箱最小且确定
    for rel in case.get("fs", {}).get("seed", []):
        s = src / rel
        d = sandbox / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        if s.exists():
            shutil.copy2(s, d)
    cwd = sandbox
```

把 `subprocess.run(..., cwd=str(cwd))` 用上面的 `sandbox`,并在构造返回 dict 时把 `"files": []` 替换为:

```python
    def _snapshot(root: Path) -> list[dict[str, str]]:
        snap = []
        for p in sorted(root.rglob("*")):
            if p.is_file():
                content = p.read_bytes()
                snap.append({
                    "path": str(p.relative_to(root)),
                    "sha256": hashlib.sha256(content).hexdigest(),
                })
        return snap
    files_snapshot = _snapshot(sandbox)
    shutil.rmtree(sandbox, ignore_errors=True)
```

返回 dict 里 `"files": files_snapshot`,并把 `sandbox` 根路径传出去(放进 `result["sandbox_root"]`)供 normalizer 用。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_runner_sandbox.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/alignment_runner.py tests/test_runner_sandbox.py
git commit -m "feat: runner injects fixture env + fs sandbox + file snapshot"
```

---

### Task 6: pytest E2E 入口——跑 case 并与 golden 比对

**Files:**
- Create: `tests/e2e/test_e2e_cases.py`
- Create: `alignment/cases/cli/version/case.json`（最小可跑 case,无需 golden 录制,纯确定性）
- Create: `alignment/golden/cli/version/golden.json`

- [ ] **Step 1: 写第一个 case + golden(手工,因为 `--version` 不需要模型)**

```json
// alignment/cases/cli/version/case.json
{
  "case_id": "cli.version",
  "priority": "P0",
  "entrypoint": {"argv": ["--version"]},
  "expected": {"exit_code": 0, "stdout_kind": "text"},
  "policy": {"match": "exact_stdout"}
}
```

```json
// alignment/golden/cli/version/golden.json
{"case_id": "cli.version", "status": "ok", "state": {"exit_code": 0}, "stdout": "2.1.88\n"}
```

> 版本号以 `hare/main.py` 的 `VERSION` 常量为准(当前 `2.1.88`);执行时核对。

- [ ] **Step 2: 写 E2E 测试(参数化遍历所有 case)**

```python
# tests/e2e/test_e2e_cases.py
import json
import sys
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "alignment"))
from alignment_runner import run_case_cli  # noqa: E402
from normalize import normalize_result      # noqa: E402

CASES = sorted((REPO / "alignment" / "cases").glob("**/case.json"))


@pytest.mark.parametrize("case_path", CASES, ids=lambda p: json.loads(p.read_text())["case_id"])
def test_case_matches_golden(case_path):
    case = json.loads(case_path.read_text(encoding="utf-8"))
    golden_path = REPO / "alignment" / "golden" / case_path.parent.relative_to(
        REPO / "alignment" / "cases") / "golden.json"
    assert golden_path.exists(), f"missing golden for {case['case_id']}: {golden_path}"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))

    result = run_case_cli(case)
    sandbox_root = result.get("sandbox_root")
    actual = normalize_result(result, sandbox_root=sandbox_root)
    expected = normalize_result(golden, sandbox_root=sandbox_root)

    assert actual["state"]["exit_code"] == expected["state"]["exit_code"], result.get("stderr")
    assert actual["stdout"] == expected["stdout"], (
        f"\n--- expected ---\n{expected['stdout']}\n--- actual ---\n{actual['stdout']}"
    )
```

- [ ] **Step 3: 跑测试确认通过**

Run: `pytest tests/e2e/test_e2e_cases.py -v`
Expected: PASS（`cli.version` 一个 case 绿）

- [ ] **Step 4: 提交**

```bash
git add tests/e2e/test_e2e_cases.py alignment/cases/cli/version/case.json alignment/golden/cli/version/golden.json
git commit -m "test: golden-based E2E case runner (cli.version green end-to-end)"
```

> **Phase 1 出口标准:** 比对管线全通——一个真实 case 从 `case.json` → 跑子进程 → normalize → 对 golden,完整闭环跑绿。

---

## Phase 2 — Mock Anthropic Server + 从 TS 原版录 golden(Layer B)

### Task 7: mock Anthropic SSE server

把 fixture 的每条 response 转成一段确定性 SSE 流(`message_start` → `content_block_start`/`delta` → `content_block_stop` → `message_delta`(带 stop_reason) → `message_stop`)。每次 POST `/v1/messages` 返回下一条 response。

**Files:**
- Create: `scripts/mock_anthropic_server.py`
- Test: `tests/test_mock_anthropic_server.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_mock_anthropic_server.py
import json
import threading
import urllib.request
from pathlib import Path
from scripts.mock_anthropic_server import make_server


def test_server_streams_fixture_response(tmp_path):
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({
        "kind": "scripted",
        "responses": [{"stop_reason": "end_turn",
                       "content": [{"type": "text", "text": "abc"}],
                       "usage": {"input_tokens": 1, "output_tokens": 1}}],
    }), encoding="utf-8")
    server = make_server(fx, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/messages",
        data=b'{"stream": true}', headers={"content-type": "application/json"})
    body = urllib.request.urlopen(req, timeout=5).read().decode()
    server.shutdown()
    assert "event: message_start" in body
    assert '"text":"abc"' in body.replace(" ", "")
    assert "event: message_stop" in body
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_mock_anthropic_server.py -v`
Expected: FAIL — `ModuleNotFoundError: scripts.mock_anthropic_server`

- [ ] **Step 3: 实现**

```python
# scripts/mock_anthropic_server.py
"""Local Anthropic-compatible SSE server that replays a fixture.

Point both hare and the TS reference at it via ANTHROPIC_BASE_URL to get the
SAME deterministic model behavior over the real HTTP path. Each POST to
/v1/messages emits the next fixture response as a synthetic SSE stream."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def _stream_response(resp: dict[str, Any]) -> bytes:
    out = bytearray()
    out += _sse("message_start", {"type": "message_start",
                                  "message": {"role": "assistant", "content": [],
                                              "usage": resp.get("usage", {})}})
    for idx, block in enumerate(resp.get("content", [])):
        out += _sse("content_block_start", {"type": "content_block_start",
                                            "index": idx, "content_block": block
                                            if block["type"] != "text"
                                            else {"type": "text", "text": ""}})
        if block["type"] == "text":
            out += _sse("content_block_delta", {"type": "content_block_delta", "index": idx,
                                                "delta": {"type": "text_delta",
                                                          "text": block["text"]}})
        out += _sse("content_block_stop", {"type": "content_block_stop", "index": idx})
    out += _sse("message_delta", {"type": "message_delta",
                                  "delta": {"stop_reason": resp.get("stop_reason", "end_turn")},
                                  "usage": resp.get("usage", {})})
    out += _sse("message_stop", {"type": "message_stop"})
    return bytes(out)


def make_server(fixture_path: str | Path, port: int = 0) -> ThreadingHTTPServer:
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    responses = list(fixture["responses"])
    cursor = {"i": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("content-length", 0))
            self.rfile.read(length)
            i = cursor["i"]
            if i >= len(responses):
                self.send_error(500, "fixture exhausted")
                return
            cursor["i"] = i + 1
            payload = _stream_response(responses[i])
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_a: Any) -> None:  # 静音
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    import sys
    p = int(sys.argv[2]) if len(sys.argv) > 2 else 8089
    srv = make_server(sys.argv[1], p)
    print(f"mock anthropic server on http://127.0.0.1:{srv.server_address[1]}")
    srv.serve_forever()
```

> SSE 事件的精确 schema 以 `hare/services/api/claude.py` 解析逻辑为准;执行时 `grep -n "message_start\|content_block_delta\|message_delta" hare/services/api/claude.py`,确保字段名一致,跑通后再录 golden。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_mock_anthropic_server.py -v`
Expected: PASS

- [ ] **Step 5: 验证 hare 真实 HTTP 路径能消费它**

Run（手动一次性验证,不进 CI):
```bash
python scripts/mock_anthropic_server.py alignment/fixtures/single_turn_hello.json 8089 &
ANTHROPIC_BASE_URL=http://127.0.0.1:8089 ANTHROPIC_API_KEY=x \
  python -m hare -p "say hi"
kill %1
```
Expected: stdout 出现 `Hello from fixture.`（证明 hare 走真实 client + mock server 也能确定性）

- [ ] **Step 6: 提交**

```bash
git add scripts/mock_anthropic_server.py tests/test_mock_anthropic_server.py
git commit -m "feat: mock Anthropic SSE server replaying fixtures (Layer B)"
```

---

### Task 8: 从 TS 原版录 golden 的脚本

**Files:**
- Create: `scripts/record_golden.py`
- Create: `docs/e2e-testing.md`

- [ ] **Step 1: 实现 record_golden.py**

```python
# scripts/record_golden.py
"""Record golden output by driving the TS reference Claude Code against the
mock Anthropic server, so hare can be diffed against the *real* reference.

Usage:
    python scripts/record_golden.py <case_id>

Reads alignment/cases/**/case.json (matching case_id), boots the mock server
with case.fixture, runs the TS CLI with ANTHROPIC_BASE_URL pointed at it,
normalizes the captured stdout/exit/files, writes alignment/golden/<...>/golden.json.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "alignment"))
from mock_anthropic_server import make_server  # noqa: E402
from normalize import normalize_result          # noqa: E402

REPO = Path(__file__).resolve().parents[1]
# 配置:TS 原版的可执行入口(按你本地安装方式改)
TS_CLI = os.environ.get("CLAUDE_TS_CLI", "claude")  # e.g. "node /path/to/cli.js"


def find_case(case_id: str) -> Path:
    for p in (REPO / "alignment" / "cases").glob("**/case.json"):
        if json.loads(p.read_text())["case_id"] == case_id:
            return p
    raise SystemExit(f"case not found: {case_id}")


def main() -> None:
    case_path = find_case(sys.argv[1])
    case = json.loads(case_path.read_text(encoding="utf-8"))
    fixture = REPO / case["fixture"]

    server = make_server(fixture, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    env = dict(os.environ)
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
    env["ANTHROPIC_API_KEY"] = "test-key-not-used"

    argv = case["entrypoint"]["argv"]
    proc = subprocess.run(
        TS_CLI.split() + argv, capture_output=True, text=True, timeout=120, env=env,
    )
    server.shutdown()

    golden = {
        "case_id": case["case_id"],
        "status": "ok" if proc.returncode == case["expected"].get("exit_code", 0) else "error",
        "state": {"exit_code": proc.returncode},
        "stdout": proc.stdout,
    }
    golden = normalize_result(golden)
    rel = case_path.parent.relative_to(REPO / "alignment" / "cases")
    out = REPO / "alignment" / "golden" / rel / "golden.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(golden, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    if golden["status"] != "ok":
        print(f"WARNING: TS exit {proc.returncode}, stderr:\n{proc.stderr}", file=sys.stderr)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 写操作手册 `docs/e2e-testing.md`**(给人看,非代码,内容要点):
  - fixture 怎么写(scripted 手写格式 + replay 怎么录);
  - 加一个新 case 的 4 步:写 `case.json` → 写/选 `fixture` → `python scripts/record_golden.py <case_id>` 录 golden → `pytest tests/e2e -k <case_id>` 跑绿;
  - `CLAUDE_TS_CLI` 怎么配(指向本地 TS 原版);
  - golden 何时需要重录(改了 fixture / 升级 TS 原版版本)。

- [ ] **Step 3: 端到端验证 cli.version 的 golden 可由 TS 重录并仍对得上**

Run:
```bash
CLAUDE_TS_CLI="<你的TS入口>" python scripts/record_golden.py cli.version
pytest tests/e2e/test_e2e_cases.py -k version -v
```
Expected: golden 重写后 `cli.version` 仍 PASS（证明 hare 与 TS 在 `--version` 上一致）

- [ ] **Step 4: 提交**

```bash
git add scripts/record_golden.py docs/e2e-testing.md
git commit -m "feat: record golden from TS reference via mock server"
```

> **Phase 2 出口标准:** 能用一条命令从 TS 原版录出任意 case 的 golden;hare 与 TS 在真实 HTTP 路径上做差分。

---

## Phase 3 — 首批四组场景 case

对每组:写 fixture → 写 case.json → `record_golden.py` 录 golden → pytest 跑绿。下面**每组给一个完整样板**,其余 case 照表复制(每条都要单独建 `case.json` + fixture + golden)。

### Task 9: 组一「CLI 基础」(P0,纯确定性,无需模型/Layer B)

这组不依赖模型,golden 可手写(或用 `run_case_cli` 跑一次 hare 输出、人工审核后冻结)。

样板已在 Task 6 完成(`cli.version`)。补齐其余:

| case_id | argv | 期望 |
|---|---|---|
| `cli.version` | `["--version"]` | exit 0,stdout = 版本号(已完成) |
| `cli.help` | `["--help"]` | exit 0,stdout 含 usage 段;`policy.match: contains` |
| `cli.bad_flag` | `["--no-such-flag"]` | exit≠0,stderr 含错误;golden 比 exit_code + stderr 关键字 |
| `cli.help_subcommand` | `["mcp", "--help"]` | exit 0,含子命令 usage |

- [ ] **Step 1:** 逐个建 `alignment/cases/cli/<name>/case.json`(照 `cli.version` 结构,改 argv 与 `policy.match`)。
- [ ] **Step 2:** 对每个 case,先 `python -c` 跑 `run_case_cli` 打印输出,人工核对无误后写进 `alignment/golden/cli/<name>/golden.json`。
- [ ] **Step 3:** `pytest tests/e2e/test_e2e_cases.py -k cli -v` 全绿。
- [ ] **Step 4:** 提交 `git commit -m "test: CLI-basics E2E cases"`

### Task 10: 组二「单轮问答」(P0)

**完整样板**(`alignment/cases/chat/single_turn/case.json`):

```json
{
  "case_id": "chat.single_turn",
  "priority": "P0",
  "entrypoint": {"argv": ["-p", "用一句话说明 hare 是什么"]},
  "fixture": "alignment/fixtures/single_turn_hello.json",
  "expected": {"exit_code": 0, "stdout_kind": "text"},
  "policy": {"match": "exact_stdout"}
}
```

- [ ] **Step 1:** 建上面的 case.json(复用 Task 3 的 fixture)。
- [ ] **Step 2:** `python scripts/record_golden.py chat.single_turn` 录 golden。
- [ ] **Step 3:** `pytest tests/e2e/test_e2e_cases.py -k single_turn -v`;若 hare 与 TS 的 stdout 包装(前后缀/换行)有差异,这正是要发现的对齐 bug——记录到 issue,必要时在 normalize 里只抹真不确定项(切勿为了过测把真实差异也抹掉)。
- [ ] **Step 4:** 再补 1-2 个变体:多段文本回复、回复含代码块。各自 fixture + golden。
- [ ] **Step 5:** 提交 `git commit -m "test: single-turn chat E2E cases"`

### Task 11: 组三「工具调用」(P0)

**完整 fixture 样板**(`alignment/fixtures/tool_read_then_answer.json`):

```json
{
  "kind": "scripted",
  "responses": [
    {"stop_reason": "tool_use",
     "content": [
       {"type": "text", "text": "我读一下 README。"},
       {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "README.md"}}
     ],
     "usage": {"input_tokens": 50, "output_tokens": 20}},
    {"stop_reason": "end_turn",
     "content": [{"type": "text", "text": "README 说明了项目用途。"}],
     "usage": {"input_tokens": 120, "output_tokens": 10}}
  ]
}
```

**case 样板**(`alignment/cases/tools/read_then_answer/case.json`):

```json
{
  "case_id": "tools.read_then_answer",
  "priority": "P0",
  "entrypoint": {"argv": ["-p", "这个项目是干什么的?"]},
  "fixture": "alignment/fixtures/tool_read_then_answer.json",
  "fs": {"seed": ["README.md"]},
  "expected": {"exit_code": 0, "stdout_kind": "text"},
  "policy": {"match": "exact_stdout", "check_files": true}
}
```

- [ ] **Step 1:** 建 fixture + case(注意 `fs.seed` 让沙箱里有 `README.md` 供 Read 工具读)。
- [ ] **Step 2:** `python scripts/record_golden.py tools.read_then_answer` 录 golden(golden 含 `files` 快照——验证工具未意外改文件)。
- [ ] **Step 3:** `pytest -k read_then_answer -v` 跑绿。
- [ ] **Step 4:** 补变体:`Bash` 工具(命令输出回灌)、写文件工具(`Write`,golden 的 `files` 应出现新文件 sha256)、工具报错路径(tool 抛错后模型如何收尾)。各自 fixture+case+golden。
- [ ] **Step 5:** 提交 `git commit -m "test: tool-use E2E cases (read/bash/write/error)"`

### Task 12: 组四「权限 + 压缩」(P1)

这组验证状态机最易错的地方。

- [ ] **Step 1: 权限拒绝流程** — fixture 让模型请求一个危险工具(如对沙箱外路径 `Write`),case 用非交互模式且配置为 deny;golden 应体现"工具被拒、模型收到拒绝反馈、最终如何回应"。建 `alignment/cases/permission/deny_write/{case.json}` + fixture + golden。
  - 先 `grep -n "permission\|deny\|allowedTools\|--dangerously" hare/main.py hare/tool.py` 找到 hare 控制权限的真实参数,据此写 argv/env。
- [ ] **Step 2: auto-compaction 触发** — fixture 构造足够长的多轮对话,或用 env 调小 token 阈值(`grep -n "compact\|token.*threshold\|maxTokens" hare/services/compact/auto_compact.py` 找触发条件),让 `autocompact` 真的触发;golden 比对压缩后是否仍产出正确最终回复、是否打印压缩提示。建 `alignment/cases/compact/auto_trigger/`。
- [ ] **Step 3: cost 统计** — case 跑完后 stdout/结果里的 cost 字段经 normalize 抹成 `<COST>` 不行(那就测不到了);改为断言 cost 字段**存在且 > 0**,或在 `policy` 里加 `check_cost_positive: true`,在 E2E 测试里特判。建 `alignment/cases/cost/single_turn_cost/`。
- [ ] **Step 4:** 各自录 golden(权限/压缩用 `record_golden.py`;cost 特判项在 pytest 里断言)。
- [ ] **Step 5:** `pytest tests/e2e -k "permission or compact or cost" -v` 跑绿。
- [ ] **Step 6:** 提交 `git commit -m "test: permission + compaction + cost E2E cases"`

> **Phase 3 出口标准:** 四组场景各有可跑、对齐 TS 的 case,`pytest tests/e2e` 全绿。

---

## Phase 4 — CI 接入 + 回归门禁 + 清理覆盖率债

### Task 13: Makefile + CI 接入

**Files:**
- Modify: `Makefile`、`.github/workflows/*.yml`

- [ ] **Step 1:** Makefile 加目标:

```makefile
e2e: ## Run deterministic E2E (Layer A, no network)
	pytest tests/e2e -v

mock-server: ## Boot mock Anthropic server (usage: make mock-server FIXTURE=path PORT=8089)
	python scripts/mock_anthropic_server.py $(FIXTURE) $(PORT)

e2e-record: ## Re-record a golden from TS reference (usage: make e2e-record CASE=cli.version)
	python scripts/record_golden.py $(CASE)
```

- [ ] **Step 2:** CI:把 `make e2e` 加进现有 pipeline(它确定性、无网络、无需 TS 原版,适合每次 PR 跑)。`e2e-record` 依赖本地 TS 原版,**不进 PR CI**;设为手动触发(`workflow_dispatch`)或本地操作,golden 文件入库当事实基线。
- [ ] **Step 3:** `make e2e` 本地与 CI 各跑一次确认绿。
- [ ] **Step 4:** 提交 `git commit -m "ci: wire deterministic E2E into pipeline"`

### Task 14: 清理 coverage-chasing 测试

判据:把被测函数实现挖空成 `return None`,该测试**不失败**,即为噪音。

- [ ] **Step 1:** 列清单——`tests/` 下 `test_hit_80.py`、`test_coverage_boost.py`、`test_coverage_final_push.py`、`test_cov_restore.py`、`test_coverage_restore.py`、`test_branch_coverage.py`、`test_branch_final_push.py`、`test_branch_gap_close.py`、`test_final_gap.py`、`test_query_engine_gaps.py`、`test_settings_gaps.py`、`test_messages_gap.py`、`test_p0p1_coverage.py`、`test_coverage_c_gate.py`、`test_auto_compact_gaps.py`、`test_builtin_plugins_final.py`。
- [ ] **Step 2:** 逐个判断:有真实行为断言的,改名并入对应模块测试;纯为覆盖率执行行、无有效断言的,删除。
- [ ] **Step 3:** 跑 `make test-unit` 确认删后仍绿;记录覆盖率从虚高回落到真实值(预期数字下降,这是好事)。
- [ ] **Step 4:** 提交 `git commit -m "test: remove coverage-chasing tests, keep behavioral ones"`

> **Phase 4 出口标准:** E2E 进 CI 每次 PR 跑;覆盖率数字重新可信;一条命令可重录 golden。

---

## Self-Review

- **Spec 覆盖:** 三支柱(Layer A 进程内 / Layer B mock server / normalizer+runner+comparator)均有任务;用户选的"TS 差分"(Phase 2)、"scripted+replay 两后端"(fixture `kind` 字段 + Task 1/7 同格式消费)、"四组场景"(Phase 3 Task 9-12)全覆盖。
- **缺失修复:** `alignment/normalize.py` 缺失(Task 4)、runner 无 fixture/沙箱(Task 5)、cases 全空(Phase 3)均已成任务。
- **类型/命名一致:** `load_fixture`/`fixture_call_model`(Task 1)在 Task 2 引用一致;`normalize_result(result, sandbox_root)`(Task 4)在 Task 6/8 调用签名一致;fixture 格式(`kind`/`responses`/`stop_reason`/`content`/`usage`)在 Task 1、3、7、10、11 一致;`make_server(fixture_path, port)`(Task 7)在 Task 8 调用一致。
- **需执行时核对的真实接口**(计划已逐处标注 grep 命令):`StreamEvent` 构造签名、hare 的 print/非交互参数、SSE 事件 schema、权限与压缩的真实参数/阈值、`VERSION` 常量。这些是"按实际代码校准"而非占位符。

---

## 执行顺序与依赖

Phase 0 → 1 → 2 → 3 → 4 严格递进(每个 Phase 的出口标准是下一个的前提)。Phase 3 的四组 case 之间互相独立,可并行。**最小可见价值**:做完 Phase 0+1(到 `cli.version` 跑绿)就已经有"确定性子进程 E2E + golden 比对"的可用闭环;Phase 2 把 oracle 从"快照"升级成"TS 差分";Phase 3 铺量;Phase 4 固化进 CI。
