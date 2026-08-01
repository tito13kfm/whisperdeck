#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -x .venv/bin/python ]]; then
    python_bin=.venv/bin/python
else
    python_bin="$(command -v python3)"
fi

echo 'Running Python tests...'
"$python_bin" -m pytest

echo 'Running JavaScript tests...'
npm test

echo 'Checking diff whitespace...'
git diff --check
git diff --cached --check

echo 'Verification passed.'
