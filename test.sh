#!/bin/bash
# WhisperDeck — Test runner for Linux/macOS
cd "$(dirname "$0")"

# Check for virtual environment
if [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
else
    PYTHON="python3"
fi

# Run pytest with any passed arguments
$PYTHON -m pytest "$@"
