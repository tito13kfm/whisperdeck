# Wrong directions, issue #340 run

## Phase 0's prior-work check searches only one namespace

The runner prompt says:

> Grep that for the issue's key identifiers (the function, field, endpoint, or
> setting it names). If a plausible commit appears, read it before
> investigating anything.

It only names `git log`. That worked here by luck: the commit subject happened
to be `fix(voice_match): honor a cancel instead of writing anyway (#331)`, and
`voice_match` is the issue's key identifier. Had the fix commit been titled
after the file or the release rather than the function, the grep would have
missed it.

The decisive artifact was a closed issue with a near-identical title, #330
"voice_match commits relabel and segment overwrite after a cancel". That is a
different namespace from the commit log, and prior work gets recorded in all
three: commits, closed issues, merged PRs.

Recommended fix, add to Phase 0 next to the existing `git log` line:

    gh issue list --state closed --limit 30 --search "<key noun from the title>"

Cost is one command, same as the existing check.

## "Already done" is treated as a whole-issue verdict

The prompt says:

> If the work is already done, stop and report that rather than re-doing it.

Read literally, that closes #340 and loses two real findings. #340's body
carried three claims: the headline (fixed), a complement-sweep instruction
(never carried out, and it finds a live bug in `rediarize`), and a nearby-note
about `completed` jobs with a non-null `error` (real, with a confirmed consumer
misread at `static/rack.js:3488`).

Recommended fix, amend that line to: when the headline defect is already fixed
but the body carries secondary notes or a sweep instruction, verify each claim
separately before closing. Report a per-claim verdict, not a per-issue one.

## Not a wrong direction, but worth recording

The prompt's insistence that the prior-work check "costs one command" is
correct, and this is the sixth confirmed instance in about a week. The
duplicate here arose because #340 was filed from an investigation branch cut
before the fix merged, so the filer could not have known. That makes the
duplicate rate a property of running investigations in parallel, and it will
keep happening regardless of filing discipline.
