// Pure kind-rotation helper for the detail-page Mode toggle (#299).
// Dependency-free and DOM-free so tests_js can require it directly.

// The order the Mode toggle steps through. voice_dump is a member rather than
// something the old `: 'meeting'` fallback swept up: a voice_dump transcript
// matched none of the previous ternary's arms, so one click rewrote its kind
// to meeting, hiding the Dump Review tab and every finalized item with it.
const KIND_CYCLE = ['meeting', 'dictation', 'voice_note', 'voice_dump'];

// Next kind in the rotation, or null when `kind` is outside it.
//
// null means "do not change the kind at all". That distinction is the fix:
// returning a default here is what made an unrecognised kind silently become
// 'meeting'. Any kind added to the schema without being added to KIND_CYCLE
// now declines to cycle instead of being rewritten.
function nextTranscriptKind(kind) {
  const at = KIND_CYCLE.indexOf(kind);
  if (at === -1) return null;
  return KIND_CYCLE[(at + 1) % KIND_CYCLE.length];
}

module.exports = { KIND_CYCLE, nextTranscriptKind };
