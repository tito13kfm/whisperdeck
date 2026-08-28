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

echo 'Checking JS/CSS bundles are up to date...'
bundle_tmp="$(mktemp -d)"
node_modules/.bin/esbuild static/rack.js --bundle --minify --sourcemap --outfile="$bundle_tmp/rack.min.js"
node_modules/.bin/esbuild static/rack.css --minify --outfile="$bundle_tmp/rack.min.css"
if ! diff -q "$bundle_tmp/rack.min.js" static/rack.min.js >/dev/null; then
    echo 'static/rack.min.js is stale - run `npm run build:js` and commit the result.' >&2
    exit 1
fi
if ! diff -q "$bundle_tmp/rack.min.css" static/rack.min.css >/dev/null; then
    echo 'static/rack.min.css is stale - run `npm run build:css` and commit the result.' >&2
    exit 1
fi
rm -rf "$bundle_tmp"

echo 'Checking diff whitespace...'
git diff --check
git diff --cached --check

echo 'Verification passed.'
