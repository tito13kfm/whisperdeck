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
# (needle, label, expect_present) -- expect_present=True means n>=1 is OK,
# False means n==0 is OK (an old pattern that must have been fully replaced).
checks = [
    ('class="empty-unit"',       "empty-unit template", True),
    ('class="modal-callout"',    "modal-callout template", True),
    ("'auth-led').classList.add('ok')", "auth-led classList.add('ok')", True),
    ('page-head--with-actions',  "page-head--with-actions class", True),
    ('transcribing a ',          "new 'working' copy phrase", True),
    ('running as one block',     "new 'working' suffix", True),
    ('no section data on this run', "OLD 'working' copy phrase (should be 0)", False),
    ('NO SEGMENTS',              "OLD ALL-CAPS empty text (should be 0)", False),
    ('font-size: 9.5px',         "OLD rail-foot 9.5px (should be 0)", False),
    ('style="gap:14px"',         "OLD detail page-head gap inline (should be 0)", False),
    ('class="btn btn--ghost btn--sm"', "ghost-sm buttons", True),
    ('class="btn btn--amber btn--sm"', "amber-sm buttons", True),
    ('class="modal-actions"',    "modal-actions class", True),
    ('class="modal-title"',      "modal-title class", True),
    ('status-badge--',           "status-badge variants", True),
    ('page-status--',            "page-status variants", True),
]
all_ok = True
for needle, label, expect_present in checks:
    n = js.count(needle)
    ok = (n >= 1) if expect_present else (n == 0)
    all_ok = all_ok and ok
    print(f"  {label:<45} -> {n}  [{'OK' if ok else 'FAIL'}]")

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

balance_ok = (open_braces == close_braces and open_parens == close_parens
              and open_brackets == close_brackets and backticks % 2 == 0)
if not (all_ok and balance_ok):
    raise SystemExit("\nFAIL: one or more checks above did not pass.")
print("\nAll checks passed.")
