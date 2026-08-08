# Wrong Directions & Discrepancies — Issue #210

1. **`esbuild` command path in `npm run build`**:
   - Discrepancy: `npm run build` executes `esbuild`, which failed in git-bash shell with `'esbuild' is not recognized as an internal or external command`.
   - Fix/Workaround: Executed `npx esbuild static/rack.js --bundle --minify --outfile=static/rack.min.js && npx esbuild static/rack.css --minify --outfile=static/rack.min.css` directly, which successfully built both `rack.min.js` and `rack.min.css`.
   - Recommendation: Update `package.json` scripts to use `npx esbuild` or `./node_modules/.bin/esbuild`.

2. **Case Sensitivity in E2E Text Assertions**:
   - Discrepancy: Initial `test_costs_ui_e2e.py` checked for `"Costs"` in `inner_text()`, but `inner_text()` returned CSS uppercase-transformed text (`"COSTS"`).
   - Fix/Workaround: Lowercased `inner_text().lower()` before asserting string containment.
