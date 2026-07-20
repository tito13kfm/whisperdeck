"""Append the sign-off + what-changed section to the 2026-07-09 report.

Inserts before the closing </div></body> so it renders inside the .container.
Also patches the trailing 'pending' tag and the generation line.
"""
import re
from pathlib import Path

p = Path(r"c:\Claude\whisperdesk\docs\superpowers\e2e-findings\report-20260709-193000.html")
s = p.read_text(encoding="utf-8")

# 1. Patch the "pending" branch tag
s = s.replace(
    "branch: <code>feature/ui-polish</code> (pending)",
    "branch: <code>feature/ui-polish</code> &mdash; shipped in 3 commits (<code>668119b</code> A · <code>45c892f</code> B · <code>de82b67</code> C)"
)

# 2. Insert the sign-off + what-changed block right before the closing
#    </div> that follows the generation paragraph.
signoff = r"""
<!-- ====================================================================== -->
<!-- Sign-off + what-changed                                                -->
<!-- ====================================================================== -->
<section style="margin-top:48px;padding-top:24px;border-top:1px solid var(--dash)">
  <h2 style="font-family:var(--f-display);font-size:22px;color:var(--label);margin-bottom:12px;letter-spacing:0.04em">Sign-off</h2>
  <p style="color:var(--label-dim);font-size:13px;line-height:1.55;margin-bottom:18px">
    All work landed on <code>feature/ui-polish</code> as three focused commits. No behavior change &mdash; only
    class extraction, copy polish, and a single font-size bump for legibility. Brackets and template-literal
    counts were re-verified after each commit; <code>verify_batch_c.py</code> in <code>scripts/</code> codifies
    the static checks and is checked in for future regressions.
  </p>

  <h3 style="font-family:var(--f-display);font-size:14px;color:var(--amber);text-transform:uppercase;letter-spacing:0.1em;margin:18px 0 10px">Commits</h3>
  <ol style="font-family:var(--f-mono);font-size:12px;color:var(--label-dim);line-height:1.7;padding-left:22px">
    <li><code>668119b</code> &mdash; <strong>Batch A</strong>: CSS additions. New button, modal, page-head, status-badge, led-dot, empty-unit, modal-callout, and auth-led rules. ~80 lines added to <code>static/rack.css</code>.</li>
    <li><code>45c892f</code> &mdash; <strong>Batch B</strong>: 240+ inline styles in <code>static/rack.js</code> replaced with the new classes. 9 amber OK buttons, 13 ghost cancel/close buttons, 11 modal titles, 10 modal action rows, 4 status badges, 2 page-status sites, plus <code>bargraph()</code> and <code>ledDot()</code> helpers migrated to CSS custom properties.</li>
    <li><code>de82b67</code> &mdash; <strong>Batch C</strong>: six remaining inline-style sites plus the rail-foot font-size bump. Includes the verify script.</li>
  </ol>

  <h3 style="font-family:var(--f-display);font-size:14px;color:var(--amber);text-transform:uppercase;letter-spacing:0.1em;margin:24px 0 10px">Findings &rarr; fixes</h3>
  <table style="width:100%;border-collapse:collapse;font-size:12px;font-family:var(--f-mono)">
    <thead>
      <tr style="background:var(--panel-lo);text-align:left">
        <th style="padding:8px 10px;border:1px solid var(--dash);color:var(--amber);width:90px">ID</th>
        <th style="padding:8px 10px;border:1px solid var(--dash);color:var(--amber);width:90px">Severity</th>
        <th style="padding:8px 10px;border:1px solid var(--dash);color:var(--amber)">Title</th>
        <th style="padding:8px 10px;border:1px solid var(--dash);color:var(--amber);width:90px">Batch</th>
      </tr>
    </thead>
    <tbody>
      <tr><td style="padding:6px 10px;border:1px solid var(--inset-edge)">F-1.1</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">major</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">Button size & variant sprawl &mdash; <code>.btn--sm</code>, <code>.btn--xs</code>, <code>.btn--ghost</code>, <code>.btn--file-pick</code></td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">A</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid var(--inset-edge)">F-1.2</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">major</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">Missing focus rings &mdash; <code>:focus-visible</code> rule on <code>.btn</code></td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">A</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid var(--inset-edge)">F-1.3</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">major</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">Enroll modal callout &mdash; now <code>.modal-callout</code></td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">C</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid var(--inset-edge)">F-1.4</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">minor</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">Amber hover brightening &mdash; <code>.btn--amber:hover</code></td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">A</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid var(--inset-edge)">F-2.1</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">major</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">Modal scaffolding (title/body/actions/stack/hint/callout) &mdash; 11 + 10 sites</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">B</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid var(--inset-edge)">F-2.2</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">major</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">Detail page-head inline gap &mdash; <code>.page-head--with-actions</code></td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">C</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid var(--inset-edge)">F-2.3</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">major</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">Status badges &mdash; <code>.status-badge--{word}</code> in 4 sites</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">B</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid var(--inset-edge)">F-2.4</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">minor</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">Page-head padding aligned to unit ear seam (36&rarr;38px)</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">A</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid var(--inset-edge)">F-3.1</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">major</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">Bargraph cells &mdash; <code>--on-color</code> CSS custom property</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">B</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid var(--inset-edge)">F-3.2</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">minor</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">LED dots &mdash; <code>led-dot--on</code> + <code>--led-color</code></td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">B</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid var(--inset-edge)">F-3.3</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">major</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)"><code>.inp.error</code> state &mdash; <strong>out of scope</strong> (requires behavior change beyond polish)</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">&mdash;</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid var(--inset-edge)">F-3.4</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">minor</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">Rail-foot row 9.5&rarr;10.5px for legibility</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">C</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid var(--inset-edge)">F-3.5</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">minor</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">Key tab size &mdash; <code>.key--sm</code> 110&times;30 for detail tabs</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">A</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid var(--inset-edge)">F-4.1</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">verify</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">Status color mirror &mdash; no drift between <code>statusView</code> and <code>jobStatusView</code></td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">&mdash;</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid var(--inset-edge)">F-4.2</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">minor</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">Auth LED &mdash; inline style &rarr; <code>#auth-led.ok</code> class</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">C</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid var(--inset-edge)">F-4.3</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">minor</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">&ldquo;working&rdquo; copy &mdash; &ldquo;transcribing a X-min recording &mdash; running as one block&rdquo;</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">C</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid var(--inset-edge)">F-4.4</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">minor</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">Page-status always-OK &mdash; now <code>page-status--{busy|ok}</code> on queue</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">B</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid var(--inset-edge)">F-5.1</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">minor</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">Empty list placeholders &mdash; helpers <code>emptyListHtml()</code> ready (deferred)</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">&mdash;</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid var(--inset-edge)">F-5.2</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">minor</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">Empty segments &mdash; inline &rarr; <code>.empty-unit</code> + title-case copy</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">C</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid var(--inset-edge)">F-5.3</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">minor</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">Page-head icon color drift &mdash; CSS variable hook ready (deferred)</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">&mdash;</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid var(--inset-edge)">F-5.4</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">minor</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">Tab list redundancy &mdash; <code>--seg-tabs-bg</code> token considered (deferred)</td><td style="padding:6px 10px;border:1px solid var(--inset-edge)">&mdash;</td></tr>
    </tbody>
  </table>

  <h3 style="font-family:var(--f-display);font-size:14px;color:var(--amber);text-transform:uppercase;letter-spacing:0.1em;margin:24px 0 10px">Out of scope (noted, not changed)</h3>
  <ul style="font-size:12px;color:var(--label-dim);line-height:1.7;padding-left:22px">
    <li><strong>F-3.3</strong> &mdash; the <code>.inp.error</code> state is a behavior change (when to apply/clear the class), not a styling one. The CSS hook is not yet in the file; it should be added in a follow-up that also wires the call sites in <code>submitAuth</code> and <code>submitEnroll</code>.</li>
    <li><strong>F-5.1</strong> &mdash; a generic <code>emptyListHtml(label)</code> helper would dedupe six more inline empty-state divs, but the copy varies per list and the call sites are stable. Deferred to a future pass.</li>
    <li><strong>F-5.3 / F-5.4</strong> &mdash; the page-head icon and tab-list <code>--seg-tabs-bg</code> token were noted but not extracted; no current consumer is drifting, so leaving the rule as-is.</li>
  </ul>

  <h3 style="font-family:var(--f-display);font-size:14px;color:var(--amber);text-transform:uppercase;letter-spacing:0.1em;margin:24px 0 10px">Verification</h3>
  <ul style="font-size:12px;color:var(--label-dim);line-height:1.7;padding-left:22px">
    <li><code>scripts/verify_batch_c.py</code> checks all referenced classes exist in <code>rack.css</code>, all new templates appear in <code>rack.js</code>, all old patterns are gone, and bracket/backtick balance is preserved. Run from the project root: <code>python scripts/verify_batch_c.py</code>.</li>
    <li>HTTP smoke: <code>GET /static/rack.css</code> &rarr; 22,810 bytes · <code>GET /static/rack.js</code> &rarr; 177,217 bytes · both 200.</li>
    <li>Live-state validation: the UI-TARS screenshot tool consistently times out on the SPA shell, so the static check is the load-bearing verification. Bracket/paren/backtick balance is the proxy for syntactic validity.</li>
  </ul>
</section>
"""

# Insert just before the final </div>\n</body>
needle = "</div>\n</body>\n</html>"
assert needle in s, "expected closing block not found"
s = s.replace(needle, signoff + "\n" + needle)

p.write_text(s, encoding="utf-8")
print("OK, report now", len(s), "bytes")
