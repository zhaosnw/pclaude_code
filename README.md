# pclaude_code

`pclaude_code` is a recovered Python-first port of the Claude Code CLI. The main Python package is published here as `hare`, and the repo also keeps a recovered frontend tree plus alignment, audit, and E2E assets used to compare Python behavior with the original TypeScript implementation.

## Current Status

- The primary Python CLI implementation lives under `hare/`.
- The current golden E2E alignment assets live under `hare/alignment/`.
- The old 519-case Phase1 / py-only corpus lives under `legacy_alignment/`.
- Canonical Python source lives at top-level `hare/`; `hare/hare/` now only remains as a thin compatibility shim for `cd hare/` workflows.

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

## Install

Install the Python package with the same extras used by CI:

```bash
python -m pip install -e ".[dev,anthropic]"
```

The CLI entrypoint is:

```bash
hare
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
