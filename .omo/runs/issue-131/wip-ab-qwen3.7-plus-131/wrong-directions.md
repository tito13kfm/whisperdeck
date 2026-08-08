# Wrong Directions

## 1. Issue body line numbers are stale

Issue says bgJobPollTimer is at line 571, bankPollTimer at 2104, detailPollTimer at 2365.
Actual current lines: 573, 2018, 2332 respectively.

**Recommendation**: Never trust issue body line numbers. Always verify against current code.

## 2. Issue missed dashPollTimer

Issue mentions 3 timers (bgJob, bank, detail) but `dashPollTimer` (line 2017, 3s interval on dashboard) has the same problem: not cleared on logout/401.

**Recommendation**: Issue body should have enumerated all polling timers, not just the ones the author noticed. Fix includes dashPollTimer.

## 3. grep tool returned no matches for timer variable names

Running `grep` for `bankPollTimer|detailPollTimer` in `static/rack.js` returned no matches, even though codegraph found them. Likely a path/encoding issue on Windows with the grep tool. Direct `read` at known line numbers worked.

**Recommendation**: When grep returns suspiciously empty results on Windows, fall back to direct `read` at known offsets from codegraph.
