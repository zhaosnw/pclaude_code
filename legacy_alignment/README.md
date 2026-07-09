# Legacy Alignment

这是旧 Phase1 / py-only 对齐链路当前的主目录。

## 当前角色

- 这里承载旧 519-case 对齐体系。
- 旧 runner / compare / verify / phase2 相关脚本与测试应以这里为准。
- 这里不是当前 golden E2E 的主目录。

## 当前内容

- `alignment_data.json`
- `cases/P0`, `cases/P1`, `cases/P2`
- `normalize.py`
- `schema/`
- `ALIGNMENT_DEFINITION.md`

## 适用场景

- 维护旧 Phase1 / py-only case
- 运行 `alignment_runner.py` / `compare_alignment.py` / `verify_alignment.py`
- 处理 `alignment_data.json` 和旧链路 gate

## 不要混用

- 当前 golden E2E 资产请看 `hare/alignment/`。
- repo-root `alignment/` 当前只是兼容镜像层，不是旧链路主目录。

## 当前状态入口

- `REVIEW_2026-07-02.md`
- `docs/alignment-status/2026-07-07.md`
