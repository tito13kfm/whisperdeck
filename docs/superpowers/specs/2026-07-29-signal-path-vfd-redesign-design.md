# Signal Path VFD redesign

Status: approved, implementing.

## Problem

The Transcribe page's "Signal path" section is six separate knob/toggle controls
(Provider, Model, Language, Mode, Speakers, Auto-Correct) plus a collapsible
"Fine adjust" `<details>` with four plain form fields (Speaker count, Meeting
title, Creativity, Context). Two problems drove this redesign:

1. The user wanted a single unified VFD-style display instead of scattered
   knobs, in the spirit of a boutique aftermarket car-stereo faceplate.
2. Fine adjust was reachable only via an unlabeled `<details>` toggle, and its
   fields were plain inputs with no relationship to the new panel.

Eighteen mockup iterations were built and reviewed in the visual companion
(`.superpowers/brainstorm/128121-1785369232/content/composite-v1..v18-*.html`,
gitignored) before arriving at this design. v18 is the reference implementation
of the final interaction model.

## Physical object, not a webpage

The panel is one fixed-size unit — width and height are literal `width`/
`height`, never `max-width`/`min-height`. This holds across every state
(browse, wheel edit, advanced list, advanced field edit). A real device
faceplate doesn't resize when you turn a knob; neither does this.

- `--panel-h: 428px`, `--row-h: 54px`, `--row-gap: 10px`.
- Outer wrap `width: 840px` (fixed).
- `min-width: 0` is required at three nested flex/grid levels (screen wrap,
  row, value column) — the CSS flex/grid default (`min-width: auto`, sized to
  content) silently overrides `max-width` otherwise. This was the root cause
  of three separate "panel keeps resizing" bugs during mockup iteration.

## Layout

Decorative steel handles (vertical, riveted) bookend a dark bezel. Inside the
bezel: a 76px-wide column of 6 square-ish metal buttons (categories), a seam,
the VFD screen, another seam, a 3-button nav column (Up chevron / OK / Down
chevron). Left buttons and screen rows share one CSS Grid row-track list
(`grid-template-rows: repeat(6, var(--row-h))`) so button-to-label alignment
never drifts as content changes.

## Color language

Reuses existing app tokens, not new hex values:

- `var(--vfd)` (#4DE8D8, cyan) — all screen text in Browse/Wheel states.
- `var(--vfd-bg)` (#050B0A) — screen background.
- `#5FCB7A` (matches existing `GREEN` const) — "selected/active/confirmed"
  signal: lit category button, active-row tick in Advanced, value color while
  actively adjusting a field.
- `#E0A83E` (matches existing `AMBER` const) — meta/price-performance info
  line only.
- `var(--f-mono)` (IBM Plex Mono) for every text element in this component.
  Earlier iterations mixed the small-VFD tube font in some sub-screens and
  caused inconsistent sizing; final design uses one font everywhere in this
  panel.

The existing `.vfd`/`vfd()` helper and `--f-tube` font stay untouched — they're
still used elsewhere (Settings knobs, Queue rows, dashboard ticker). This
component gets its own class prefix (`mfd-*`) so it doesn't collide with or
alter those.

## States

**Browse** (default): 6 rows, tick + label + value. Pressing a binary
category (Speakers, Auto-Correct) toggles instantly and flashes green — no
sub-screen. Pressing a multi-option category (Provider, Model, Language,
Mode) enters Wheel edit for that category; the other 5 left buttons dim and
become inert until the active one is pressed again (closes back to Browse).

**Wheel edit**: centered ghost-prev / current-value / ghost-next roulette.
Up/Down cycles, OK confirms back to Browse. Info band below shows the
category's description and, for Provider/Model, a price/performance meta
line. Overflowing values marquee: measured overflow in px via
`scrollWidth - clientWidth`, a uniquely-named `@keyframes` rule is generated
per element with that exact distance baked in, and the animation runs on the
value span. The value span must be `display:inline-block` — `transform` has
no effect on plain inline elements per the CSS spec; this was the actual bug
behind "it doesn't marquee, it just cuts off" in v16.

**Advanced (Fine Adjust)**: OK from Browse root (nothing selected) opens it.
Screen shows a 5-row list reusing the same row grid: Speaker count, Meeting
title, Creativity, Context, ◄ Back to Browse. Up/Down moves a highlight
(green tick + green label, mirrors the Browse category-button idiom) among
the 5 rows; OK enters the highlighted row. All 6 left category buttons are
dimmed/inert for the whole time Advanced is open. Bottom hint line always
says "OK: Advanced" while at Browse root, so the entry point is finally
discoverable (previously zero affordance existed).

- **Speaker count** and **Creativity** are modeled as wheel fields (same
  roulette pattern as the main categories: Speaker count = Auto-detect,1..12;
  Creativity = 0..10 with "Strict"/"Balanced"/"Creative" labels at 0/5/10).
  Up/Down adjusts, OK confirms back to the field list.
- **Meeting title** and **Context** are free text. OK on the row focuses a
  real `<input>`/`<textarea>` (Context is multiline) styled as VFD text
  (green, glow, `caret-color` green, monospace). Typing works, but the
  expected real-world path is paste — nobody is going to hunt-and-peck a
  meeting agenda through a roulette wheel. Enter confirms the single-line
  title field; Context (multiline) confirms only via the OK button since
  Enter must stay available for line breaks.
- OK on the Back row closes Advanced and returns to Browse root.

## Data model mapping

No new state shape for the six main categories — they already map directly
to `S.providerIdx` / `S.modelIdx` / `S.langIdx` / `S.mode` / `S.diarize` /
`S.autoCorrect`.

Advanced fields move from DOM-read (`$('tx-speakers').value` etc., only
correct because those inputs are permanently in the DOM today) to app state,
since the new inputs are transient — they only exist in the DOM while that
one field is being edited:

- `S.advSpeakerCount`: `null` (Auto) or integer 1-12.
- `S.advTitle`: string, `''` default.
- `S.advTemperature`: integer 0-10 (wheel steps); converted to `/10` float
  (0.0-1.0) when building the form data in `startJob()`. Existing backend
  field is `temperature: float`, no range enforced server-side beyond that.
- `S.advContext`: string, `''` default.

`startJob()` reads these four from `S` instead of querying DOM elements.
`tx-speakers`/`tx-title`/`tx-temp`/`tx-context` element IDs go away entirely.

## Integration points

- `static/rack.js`: replace the "signal path" `<div class="unit">` block
  (currently ~line 1623-1659) and the "fine adjust" `<details>` block
  (currently ~line 1661-1687) with the new panel markup + render/wire
  functions. `wireTranscribe()`/`syncTranscribe()`/`startJob()` updated
  accordingly. Stays inside the existing `.unit` wrapper for consistent page
  spacing.
- `static/rack.css`: new `mfd-*` rules (bezel, handles, buttons, chevrons,
  screen, rows, wheel, advanced list, VFD input). Existing `.vfd`, `.ctl`,
  `.knob-*`, `.tog-*`, `.field`, `.inp` rules are untouched (still used by
  Settings/Queue/dashboard).
- `static/rack.min.js`: rebuilt via esbuild after every source change.
- `scripts/capture_screenshots.py`: currently waits on `#ctl-diarize` and
  fills `#tx-title`/`#tx-speakers` through the old `<details>` toggle. Update
  to wait on the new panel's presence and set `S.advTitle`/
  `S.advSpeakerCount` directly via `page.evaluate()` + `syncTranscribe()`,
  matching the pattern the script already uses for `S.diarize`/
  `S.autoCorrect`/`S.modelIdx`.

## Out of scope

- No changes to provider/model/language data (`S.providers`, `LANGUAGES`) or
  backend cost/pricing logic (shipped separately in #209/PR #225).
- No changes to the VU meter / oscilloscope instruments block above this
  section (fixed separately in PR #226).
