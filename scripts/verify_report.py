"""Quick structural sanity check of the updated report."""
from pathlib import Path
p = Path(r"c:\Claude\whisperdesk\docs\superpowers\e2e-findings\report-20260709-193000.html")
s = p.read_text(encoding="utf-8")
print(f"size: {len(s)} bytes")
print(f"ends with </body></html>: {s.rstrip().endswith('</body></html>')}")
print(f"has Sign-off heading: {'Sign-off' in s}")
print(f"has 668119b (Batch A): {'668119b' in s}")
print(f"has 45c892f (Batch B): {'45c892f' in s}")
print(f"has de82b67 (Batch C): {'de82b67' in s}")
print(f"has all 21 finding IDs: {all(f'F-{n}.{m}' in s for n, m in [(1,1),(1,2),(1,3),(1,4),(2,1),(2,2),(2,3),(2,4),(3,1),(3,2),(3,3),(3,4),(3,5),(4,1),(4,2),(4,3),(4,4),(5,1),(5,2),(5,3),(5,4)])}")
print(f"has 'Out of scope' section: {'Out of scope' in s}")
print(f"has 'Verification' section: {'Verification' in s}")
print(f"has verify_batch_c reference: {'verify_batch_c.py' in s}")
# Quick balance check on key tags
import re
for tag in ['section', 'table', 'tbody', 'tr', 'ol', 'ul', 'li']:
    opens = len(re.findall(rf'<{tag}\b', s))
    closes = len(re.findall(rf'</{tag}>', s))
    flag = 'OK' if opens == closes else 'MISMATCH'
    print(f"  <{tag}>: {opens} open, {closes} close [{flag}]")
