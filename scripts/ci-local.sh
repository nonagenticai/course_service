#!/usr/bin/env bash
# ci-local.sh — Mirror of GitHub Actions "Lint & Unit Tests" job (.github/workflows/ci-cd.yaml).
#
# This script reproduces the per-PR Lint+Unit pipeline locally so that
# `bandit # noqa` ≠ `ruff # noqa` style drifts between local pre-commit and CI
# can never sneak past us again. Run this before pushing.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# === Install dependencies ===
uv sync --all-extras

# === Run ruff linter ===
uv run ruff check src/ tests/

# === Run ruff formatter (check only) ===
uv run ruff format --check src/ tests/

# === Run ty type checking ===
uv run ty check src/

# === Run unit tests ===
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://postgres:postgres@127.0.0.1:5432/postgres}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
export STORAGE_LOCAL_PATH="${STORAGE_LOCAL_PATH:-/tmp/test_courses}"
mkdir -p test-results
uv run pytest tests/ -v --tb=short -x -m "not integration" \
    --cov=src --cov-report=xml --cov-report=term-missing \
    --junitxml=test-results/unit-tests.xml

# === Security scan - private references ===
bash scripts/ci/no-private-refs.sh

# === Security scan - pip-audit ===
uv run pip-audit

# === Security scan - bandit ===
uv run bandit -r src/ -c pyproject.toml

echo
echo "ci-local.sh: all Lint+Unit steps passed"
