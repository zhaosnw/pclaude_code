# De-Mirroring Checklist

## 当前目标

把 repo-root `alignment/` 从“兼容镜像层”进一步收口为：

- 要么完全退出，只保留 `hare/alignment/` 作为唯一 E2E 权威目录
- 要么继续保留，但只作为明确受约束的兼容层，不再成为任何新代码的默认入口

## 当前已完成

- 旧 Phase1 / py-only 资产已经从 root `alignment/` 收口到 `legacy_alignment/`
- repo root 的 `alignment_data.json` 已删除
- root `alignment/` 只剩 E2E 相关镜像资产与 `golden_normalize.py`
- `tests/test_alignment_e2e_mirror.py` 已要求：
  - `alignment/` 与 `hare/alignment/` 除 `README.md` / `__pycache__` 外逐字节一致
- root 侧活代码入口已基本不再直接 import root `alignment/`
- E2E case 资产当前已完成 fixture 字段 canonicalize：
  - `alignment/cases/**/case.json`
  - `hare/alignment/cases/**/case.json`
  中全部带 fixture 的 case，当前都已改为
  `"fixture": "hare/alignment/fixtures/<name>.json"`
- `scripts/e2e_runner.py` 与 `scripts/record_golden.py` 当前都同时兼容：
  - `alignment/fixtures/<name>.json`
  - `hare/alignment/fixtures/<name>.json`

## 当前确认的剩余依赖

### A. 必须先明确的契约

1. nested `hare/` 子项目内仍有大量相对 `alignment/` 的路径。
   这些路径在 nested 项目语境下，实际指向的是 `hare/alignment/`，不是 repo-root `alignment/`。
   所以它们不是 root 镜像层的直接 blockers，但说明“alignment”这个名字仍是 nested 项目的内部契约。

### B. 当前可接受的兼容保留

1. root `alignment/` 仍是 Git 已跟踪资产。
2. `tests/test_alignment_e2e_mirror.py` 当前把它当成一个被校验的兼容镜像层。
3. `alignment/README.md`、`hare/alignment/README.md` 已明确：
   - `hare/alignment/` 是主编辑面
   - root `alignment/` 是兼容镜像层

### C. 仍需统一的文档/帮助口径

1. `REVIEW_2026-07-02.md` 仍在讨论 root `alignment/` 是否进一步改名或归档。
2. `docs/alignment-status/2026-07-07.md` 记录了当前镜像层状态，但还没有给出去镜像化的明确完成条件。
3. 部分帮助/注释文本虽然已不影响运行，仍保留了较宽泛的 “alignment” 说法。

## 真正去镜像化前的完成条件

### 条件 1

fixture 路径契约虽然已经 canonicalize，但兼容层还没有完全退出。

- 当前主契约已经是 `"fixture": "hare/alignment/fixtures/<name>.json"`
- 运行入口仍兼容旧的 `"alignment/fixtures/..."`，这是为了平滑迁移
- 按当前工作树复查，旧前缀的活跃剩余面主要已经缩到：
  - `scripts/e2e_runner.py`
  - `scripts/record_golden.py`
  - `tests/test_e2e_runner.py`
  - `tests/test_record_golden_paths.py`
  - 以及 `hare/` 下对应副本
- 这层剩余面当前还有 allowlist 守卫测试，避免旧前缀重新扩散到其他活代码路径
- 真正去镜像化前，需要决定这层旧字符串兼容要保留多久

### 条件 2

明确 nested `hare/` 是否继续保留自包含 `alignment/` 目录命名。

- 如果继续保留：
  - repo-root `alignment/` 可以退出
  - 但 nested `hare/alignment/` 仍会存在
- 如果也要改名：
  - 影响面会更大，需单独规划

补充现状：

- repo-root `alignment/` 目录本身的直接活跃依赖面，当前也已经有 allowlist 守卫测试
- nested `hare/tests/*` 中几处原先写成 `REPO / "alignment"` 的测试入口，当前也已经收成更明确的子项目语义路径
- 这说明镜像层的直接调用面已经被压缩到少数已知测试入口，而不是散落在大量活代码里
- 但这不代表 `tests/` 与 `hare/tests/` 的同名文件问题已经整体解决；按当前工作树扫描，重复 basename 仍有 `104` 组。

### 条件 3

给 root `alignment/` 的退出方式一个明确选择：

1. 直接删除，并让所有入口只认 `hare/alignment/`
2. 改名为更明确的兼容目录，例如 `alignment_mirror/`
3. 保留现名，但长期作为镜像层并继续由测试强制一致

### 条件 4

在做最终退出前，至少要保留以下验证：

- `python -m pytest tests/test_alignment_e2e_mirror.py -q`
- `python -m pytest tests/e2e/test_e2e_cases.py tests/test_e2e_runner.py -q`
- `python -m pytest tests/test_print_mode_json.py -q`

## 当前最小结论

截至 2026-07-07，仓库已经达到：

- root `alignment/` 不再承载旧 Phase1 资产
- 新代码默认入口基本不再落到 root `alignment/`
- root `alignment/` 当前可被视为“被测试约束的兼容镜像层”
- E2E case 的 fixture 字段主契约已经切到 `hare/alignment/fixtures/...`

还没有达到：

- 可以无争议地删除 root `alignment/`
- 可以无争议地删除旧 `alignment/fixtures/...` 字符串兼容层
