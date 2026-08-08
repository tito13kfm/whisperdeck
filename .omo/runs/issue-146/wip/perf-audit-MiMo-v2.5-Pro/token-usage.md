# Token Usage — Issue #146

## Key optimizations applied
- Static source-level check before any live server/browser cycle (no browser needed for this feature)
- `from_end=true` on all background agent output collection

## Token hotspots
- **2 explore agents** (Phase 1): ~1m each, parallel. Found main JS entry point and static serving config.
- **Direct reads** (Phase 1 deep dive): app.py cache middleware, index.html, rack.js tail. Faster than agents for known files.
- **grep for serviceWorker**: Confirmed no existing SW before implementing.

## What would cut it next time
- codegraph_explore could have answered "where is rack.js loaded?" in one call instead of an explore agent.
- For simple file-location tasks, direct glob/read is cheaper than agent dispatch.
