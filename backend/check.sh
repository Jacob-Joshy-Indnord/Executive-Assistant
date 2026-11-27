#!/usr/bin/env sh
# One command for the back end: formatting, lint, types and tests with coverage.
set -eu
cd "$(dirname "$0")"
black --check .
ruff check .
mypy assistant standins contract
pytest --cov --cov-report=term-missing
