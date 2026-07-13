.PHONY: help install test test-unit test-integration test-alignment e2e test-live mock-server e2e-record lint format typecheck typecheck-full mypy-regression security clean coverage stub-check stub-priority align-check imports-check alignment-quick alignment-full alignment-guardrails audit-sizediff replay all

export PYTHONPATH := ..$(if $(PYTHONPATH),:$(PYTHONPATH))

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

install: ## Install hare with dev + anthropic (matches CI)
	cd .. && python -m pip install -e ".[dev,anthropic]"

# ---------------------------------------------------------------------------
# Testing (hierarchical: unit → integration → alignment)
# ---------------------------------------------------------------------------

test: test-unit test-integration ## Run all tests

test-unit: ## Run fast unit tests only (no network, no subprocess)
	python -m pytest tests/ \
		-m "not integration and not slow and not alignment" \
		-v --tb=short --strict-markers \
		--cov=hare --cov-report=term-missing \
		--timeout=60

test-integration: ## Run integration tests
	python -m pytest tests/ \
		-m "integration" \
		-v --tb=long --strict-markers \
		--cov=hare --cov-append --cov-report=term-missing \
		--timeout=120

test-alignment: ## Run behavioral alignment tests (pytest marker: alignment)
	python -m pytest tests/ \
		-m "alignment" \
		-v --tb=long --strict-markers

e2e: ## Run deterministic CLI E2E (Layer A fixtures, no network, no billing)
	python -m pytest tests/e2e/ -v --tb=short --strict-markers --timeout=120

test-live: ## Run live end-to-end smoke vs a REAL model (needs ANTHROPIC_* backend; costs tokens)
	HARE_LIVE_TESTS=1 python -m pytest tests/live/ -v --tb=short --timeout=200

mock-server: ## Boot mock Anthropic SSE server (usage: make mock-server FIXTURE=hare/alignment/fixtures/x.json PORT=8089)
	python scripts/mock_anthropic_server.py $(FIXTURE) $(PORT)

e2e-record: ## Re-record a case's golden from the TS reference (needs CLAUDE_TS_CLI; usage: make e2e-record CASE=chat.single_turn)
	python scripts/record_golden.py $(CASE)

test-all: ## Run all tests with full coverage
	python -m pytest tests/ \
		-v --tb=long --strict-markers \
		--cov=hare --cov-report=html --cov-report=term-missing \
		--timeout=120

# ---------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------

lint: ## Run ruff linter
	ruff check hare/ tests/

format: ## Run ruff formatter
	ruff format hare/ tests/

format-check: ## Check formatting without changing files
	ruff format --check hare/ tests/

typecheck: mypy-regression ## Run scoped mypy regression gate

typecheck-full: ## Show full scoped mypy diagnostics
	cd .. && mypy hare/ --ignore-missing-imports --show-error-codes --warn-unreachable --exclude '^(hare/(hare|tests|frontend|docs|scripts)(/|$$)|hare\.egg-info/)'

mypy-regression: ## Check mypy error count against baseline (497)
	cd .. && python scripts/check_mypy_regression.py --baseline 497

security: ## Run bandit (high severity + high confidence, same gate as CI)
	bandit -r hare/ --severity-level high --confidence-level high

# ---------------------------------------------------------------------------
# Alignment & completeness checks
# ---------------------------------------------------------------------------

stub-check: ## Count stubs, TODOs, NotImplementedErrors
	python scripts/detect_stubs.py

align-check: ## Verify alignment data integrity
	python scripts/verify_alignment.py

align-regressions: ## Check for alignment regressions
	python scripts/check_alignment_regressions.py

imports-check: ## Validate all hare imports resolve
	python scripts/validate_imports.py

# ---------------------------------------------------------------------------
# Combined CI run (what runs on PR)
# ---------------------------------------------------------------------------

all: lint format-check imports-check stub-check test-unit test-integration test-alignment mypy-regression security ## Full CI pipeline

# ---------------------------------------------------------------------------
# Alignment (TS ↔ Python behavioral comparison)
# ---------------------------------------------------------------------------

alignment-quick: ## Run P0 alignment oracle and compare (Python only)
	python scripts/alignment_runner.py --priority P0 --out /tmp/py-alignment.jsonl
	python scripts/compare_alignment.py --ts /dev/null --py /tmp/py-alignment.jsonl --priority P0 --py-only --weighted-min 1.00

alignment-full: ## Run P0+P1 alignment (Python only)
	python scripts/alignment_runner.py --priority P0,P1 --out /tmp/py-alignment.jsonl
	python scripts/compare_alignment.py --ts /dev/null --py /tmp/py-alignment.jsonl --priority P0,P1 --py-only --weighted-min 1.00

alignment-guardrails: ## Run the de-mirroring / fixture-canonicalization guardrail suite
	python -m pytest \
		../tests/test_record_golden_paths.py \
		tests/test_hare_record_golden_paths.py \
		../tests/test_alignment_case_fixture_paths.py \
		tests/test_hare_alignment_case_fixture_paths.py \
		../tests/test_e2e_runner.py \
		tests/test_hare_e2e_runner.py -q

replay: ## Replay a single alignment case (usage: make replay CASE=cli.version.both_flags)
	python scripts/alignment_runner.py --case $(CASE) --out /tmp/py-alignment.jsonl
	python scripts/compare_alignment.py --ts /dev/null --py /tmp/py-alignment.jsonl --priority P0,P1,P2,P3

audit-sizediff: ## Run TS↔Py size diff audit
	python scripts/audit_sizediff.py

stub-priority: ## Detect stubs by priority (P0=0, P1=0 gate)
	python scripts/detect_stubs_priority.py --p0-fail

gen-priority: ## Auto-assign P0-P3 priorities in legacy_alignment/alignment_data.json
	python scripts/gen_alignment_priority.py

coverage-p0p1: ## Gate P0/P1 module coverage from coverage.xml
	python scripts/coverage_p0p1_gate.py --coverage-xml coverage.xml --min-line 0.90 --min-branch 0.80

freeze-baseline: ## Freeze alignment baseline for release comparison
	python scripts/freeze_baseline.py --output baseline.json

property: ## Run Hypothesis property-based invariant tests
	python -m pytest tests/property/ -v --tb=short

property-quick: ## Run property tests with fewer examples (fast)
	python -m pytest tests/property/ -v --tb=short --hypothesis-profile=ci

alignment-cases: ## Run all legacy_alignment/cases through the Python oracle
	python scripts/alignment_runner.py --priority P0,P1,P2 --out /tmp/py-alignment.jsonl
	python scripts/compare_alignment.py --ts /dev/null --py /tmp/py-alignment.jsonl --priority P0,P1,P2

alignment-p0-run: ## Run P0 cases and compare (Python only)
	python scripts/alignment_runner.py --priority P0 --out /tmp/py-alignment-p0.jsonl
	python scripts/compare_alignment.py --ts /dev/null --py /tmp/py-alignment-p0.jsonl --priority P0

mocks-test: ## Test alignment_mocks module can be imported
	python -c "from hare.scripts.alignment_mocks import run_query_case, make_query_deps; print('alignment_mocks OK')"

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

clean: ## Remove build artifacts, __pycache__, .pyc files
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete
	find . -type f -name '*.pyo' -delete
	rm -rf build/ dist/ *.egg-info/ .mypy_cache/ .pytest_cache/ .ruff_cache/ htmlcov/ .coverage coverage*.xml

build: ## Build wheel
	python -m build --wheel
