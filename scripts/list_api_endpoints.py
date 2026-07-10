"""List all API endpoints the SPA calls."""
import re
from pathlib import Path
js = Path(r"c:\Claude\whisperdesk\static\rack.js").read_text(encoding="utf-8")
# Match `fetch('...')` and `fetch(`...`)` and "fetch('...')" with simple literal paths
hits = re.findall(r"fetch\(([`'\"`])([^`'\"`]+?)\1", js)
endpoints = sorted({h[1] for h in hits if "/api/" in h[1]})
for ep in endpoints:
    print(ep)
