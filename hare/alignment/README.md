# Hare Alignment

这是 **`hare/` 子项目层** 当前主用的 golden E2E 资产目录。

## 当前职责

这里主要承载较新的 E2E 差分资产：

- `cases/`
- `golden/`
- `fixtures/`
- `seeds/`
- `golden_normalize.py`

当前 `tests/e2e/` 与 `hare/scripts/record_golden.py` 这一套链路，都是围绕这里运行的。

同时，repo-root `alignment/` 当前保留为兼容镜像层；除 `README.md` 与缓存文件外，两边 E2E 资产应保持逐字节一致。

## 当前状态入口

- `REVIEW_2026-07-02.md`
- `docs/alignment-status/2026-07-07.md`

## 适用场景

如果你在做下面这些事情，优先看这里：

- 新增 / 维护 golden E2E case
- 录制 TS reference golden
- 对齐 headless / print / tool / json / stream-json 行为

## 不要混用

这里 **不是** 旧 Phase1 / py-only 的 519-case 目录。

旧链路请看：

- `legacy_alignment/`

如果把这里和 `legacy_alignment/` 混用，runner / case schema 很容易再次打架。
