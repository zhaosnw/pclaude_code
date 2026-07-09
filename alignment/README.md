# Root Alignment

这是 repo-root 层的 `alignment/` 目录。

## 当前角色

- 这里不是新 E2E 资产的主编辑面。
- 当前主编辑面是 `hare/alignment/`。
- 这里保留为 repo-root 兼容镜像层。

## 当前内容

- `cases/`
- `golden/`
- `fixtures/`
- `seeds/`
- `golden_normalize.py`

## 当前规则

- 新的 golden E2E 资产应以 `hare/alignment/` 为准。
- root `alignment/` 与 `hare/alignment/` 除 `README.md` 和缓存文件外应保持一致。
- `tests/test_alignment_e2e_mirror.py` 负责约束这层镜像不再漂移。

## 相关目录

- `hare/alignment/`：当前 golden E2E 主资产目录
- `legacy_alignment/`：旧 Phase1 / py-only 资产目录

## 当前状态入口

- `REVIEW_2026-07-02.md`
- `docs/alignment-status/2026-07-07.md`
- `docs/alignment-status/2026-07-06-demirroring-checklist.md`
