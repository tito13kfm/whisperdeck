// One predicate for both the per-line "?" marker and the detail header's
// "N uncertain" count -- a threshold tweak in one place must move both.
// Kept dependency-free (no DOM/global references) so it can be unit-tested
// directly in Node (tests_js/confidence.test.js) without loading rack.js's
// browser-only code. esbuild inlines it into the bundle at build time.
//
// speaker_confidence semantics:
//   [0, 1]        diarizer confidence in the label it assigned
//   -1            user-assigned sentinel: the user retagged this line by hand
//                 (POST /segments/retag, issue #305). A human override is not
//                 "uncertain", so it must never render the "?" marker or count
//                 toward "N uncertain"; the >= 0 guard excludes it.
//   null / absent never diarized; nothing to be uncertain about.
// The sentinel value is mirrored in services/relabel.py
// (USER_ASSIGNED_CONFIDENCE) -- change both together.
const LOW_CONFIDENCE_THRESHOLD = 0.5;
const USER_ASSIGNED_CONFIDENCE = -1;

function isLowConfidence(sg) {
  return sg.speaker_confidence != null
    && sg.speaker_confidence >= 0
    && sg.speaker_confidence < LOW_CONFIDENCE_THRESHOLD;
}

module.exports = { LOW_CONFIDENCE_THRESHOLD, USER_ASSIGNED_CONFIDENCE, isLowConfidence };
