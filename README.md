# pclaude_code

`pclaude_code` is a recovered Python-first port of the Claude Code CLI. The main Python package is published here as `hare`, and the repo also keeps a recovered frontend tree plus alignment, audit, and E2E assets used to compare Python behavior with the original TypeScript implementation.

## Current Status

- The primary Python CLI implementation lives under `hare/`.
- The current golden E2E alignment assets live under `hare/alignment/`.
- The old 519-case Phase1 / py-only corpus lives under `legacy_alignment/`.
- Canonical Python source lives at top-level `hare/`; `hare/hare/` is no longer part of the supported runtime layout.

For the latest verified repo status, start with:

- [REVIEW_2026-07-02.md](REVIEW_2026-07-02.md)
- [docs/alignment-status/2026-07-08.md](docs/alignment-status/2026-07-08.md)

## What is in this repo

- `hare/`: main Python package and CLI implementation
- `frontend/`: recovered frontend / TS source tree kept for reference and parity work
- `tests/`: unit, integration, property, E2E, and live smoke tests
- `hare/alignment/`: current E2E fixtures, goldens, seeds, and golden-testing assets
- `legacy_alignment/`: legacy 519-case Phase1 / py-only alignment corpus
- `scripts/`: alignment runners, mock servers, regression checks, and audit helpers
- `docs/`: notes on alignment findings and E2E testing

## Requirements

- Python 3.11+
- `pip`
- Optional: Node.js if you want to inspect or work on the frontend sources
- Optional: Anthropic credentials for live smoke tests

## 安装与启动 Hare Coding Agent

在仓库根目录执行以下命令安装运行所需依赖。这里的可编辑安装会注册 `hare` 命令；不要进入 `hare/` 子目录启动。

```bash
python -m pip install -e ".[anthropic]"
```

如需运行本仓库的测试、lint 等开发工具，再安装开发额外依赖：

```bash
python -m pip install -e ".[dev,anthropic]"
```

安装后，先确认命令可用：

```bash
python -m hare --version
```

启动交互式 Coding Agent（在你希望它操作的项目目录执行）：

```bash
hare
```

首次发起模型请求前，需要配置可用的 Anthropic 兼容凭证。例如使用环境变量：

```bash
export ANTHROPIC_API_KEY="your-api-key"
hare
```

也可以将凭证放在用户级 `~/.hare/settings.json`（不要提交）中：

```json
{
  "env": {
    "ANTHROPIC_API_KEY": "your-api-key"
  }
}
```

Hare 同时读取项目级 `.hare/settings.json`；项目配置只应包含可安全共享的非密钥设置。

非交互模式适合脚本或一次性任务：

```bash
hare -p "分析当前项目，并给出下一步建议"
```

常用启动选项：

```bash
hare --cwd /path/to/project              # 指定工作目录
hare --model <model>                     # 指定模型
hare --permission-mode plan              # 仅规划，不执行修改
hare -c                                  # 继续最近一次会话
hare --resume <session-id>               # 恢复指定会话
hare --help                              # 查看完整参数说明
```

## Common Commands

Useful `make` targets:

```bash
make install
make test-unit
make test-integration
make test-alignment
make e2e
make all
```

Equivalent direct commands exist in the `Makefile` if you prefer `pytest` or standalone scripts.

## Test Layout

- Unit tests: fast checks under `tests/` excluding integration / slow / alignment markers
- Integration tests: subprocess, filesystem, or broader workflow coverage
- Alignment tests: behavioral comparisons using fixtures and golden outputs
- E2E tests: deterministic CLI flows under `tests/e2e/`
- Live tests: real-model smoke tests under `tests/live/`

Run the full Python test suite:

```bash
make test
```

Run the full CI-style validation suite:

```bash
make all
```

## Alignment Workflow

This repo keeps two alignment tracks:

- `hare/alignment/` for the current golden-based CLI E2E differential tests
- `legacy_alignment/` for the older Phase1 / py-only oracle corpus

New assets and default workflows should target `hare/alignment/`.

Examples:

```bash
make alignment-quick
make alignment-full
make replay CASE=cli.version.both_flags
make align-check
make align-regressions
```

For a single mock SSE server:

```bash
make mock-server FIXTURE=hare/alignment/fixtures/single_turn_hello.json PORT=8089
```

## Frontend Notes

The `frontend/` tree is included mainly for parity analysis, reference, and recovered-source investigation. It is not required for ordinary Python package development.

Large generated dependency folders such as `node_modules/` are intentionally ignored and should not be committed.

## Repository Hygiene

- Do not commit `node_modules/`, coverage artifacts, or Python cache files
- Put new golden E2E assets under `hare/alignment/`
- Keep old Phase1-style oracle assets under `legacy_alignment/`
- Prefer `make` targets when you want behavior consistent with the existing workflow

## Related Files

- [Makefile](Makefile)
- [REVIEW_2026-07-02.md](REVIEW_2026-07-02.md)
- [docs/alignment-status/README.md](docs/alignment-status/README.md)
- [docs/alignment-findings.md](docs/alignment-findings.md)
- [docs/e2e-testing.md](docs/e2e-testing.md)
- [pyproject.toml](pyproject.toml)
