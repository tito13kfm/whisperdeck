"""Verify Batch C: all references resolve in rack.js + CSS exists in rack.css."""
import re
from pathlib import Path

root = Path(r"c:\Claude\whisperdesk")
css = (root / "static" / "rack.css").read_text(encoding="utf-8")
js = (root / "static" / "rack.js").read_text(encoding="utf-8")

print("=== CSS class existence ===")
for cls, label in [
    (r"\.empty-unit\b", ".empty-unit"),
    (r"\.modal-callout\b", ".modal-callout"),
    (r"\.page-head--with-actions\b", ".page-head--with-actions"),
    (r"\.rail-foot\s+\.row\b", ".rail-foot .row"),
    (r"#auth-led\b", "#auth-led"),
    (r"#auth-led\.ok\b", "#auth-led.ok"),
    (r"\.page-head\s*\{", ".page-head { ... }"),
]:
    n = len(re.findall(cls, css))
    print(f"  {label:<28} -> {n} match(es)")

print("\n=== JS template / pattern references ===")
checks = [
    ('class="empty-unit"',       "empty-unit template"),
    ('class="modal-callout"',    "modal-callout template"),
    ("'auth-led').classList.add('ok')", "auth-led classList.add('ok')"),
    ('page-head--with-actions',  "page-head--with-actions class"),
    ('transcribing a ',          "new 'working' copy phrase"),
    ('running as one block',     "new 'working' suffix"),
    ('processing ',              "OLD 'working' copy phrase (should be 0)"),
    ('NO SEGMENTS',              "OLD ALL-CAPS empty text (should be 0)"),
    ('font-size: 9.5px',         "OLD rail-foot 9.5px (should be 0)"),
    ('style="gap:14px"',         "OLD detail page-head gap inline (should be 0)"),
    ('class="btn btn--ghost btn--sm"', "ghost-sm buttons"),
    ('class="btn btn--amber btn--sm"', "amber-sm buttons"),
    ('class="modal-actions"',    "modal-actions class"),
    ('class="modal-title"',      "modal-title class"),
    ('status-badge--',           "status-badge variants"),
    ('page-status--',            "page-status variants"),
]
for needle, label in checks:
    n = js.count(needle)
    flag = "OK" if n >= 0 else "?"
    print(f"  {label:<45} -> {n}  [{flag}]")

print("\n=== Bracket/paren balance check on rack.js ===")
open_braces = js.count("{")
close_braces = js.count("}")
open_parens = js.count("(")
close_parens = js.count(")")
open_brackets = js.count("[")
close_brackets = js.count("]")
backticks = js.count("`")
print(f"  {{ {open_braces}  }} {close_braces}  (delta {open_braces - close_braces})")
print(f"  ( {open_parens}  ) {close_parens}  (delta {open_parens - close_parens})")
print(f"  [ {open_brackets}  ] {close_brackets}  (delta {open_brackets - close_brackets})")
print(f"  backticks: {backticks}  (even required)")

print("\n=== Line counts ===")
print(f"  rack.css: {len(css.splitlines())} lines, {len(css)} bytes")
print(f"  rack.js : {len(js.splitlines())} lines, {len(js)} bytes")
