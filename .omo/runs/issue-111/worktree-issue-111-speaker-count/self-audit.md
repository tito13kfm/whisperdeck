# Self-audit, issue #111

Branch: `worktree-issue-111-speaker-count`. Every citation below was re-opened
and confirmed present, not marked from memory of intent.

**This workflow runs no independent-model audit pass.** Everything below is
self-review by the same orchestrator (Opus) that wrote the fix. The one
outside opinion in this run was the Phase 1.5 completion-race check (Fable),
which was scoped to the cancellation/completion question, not to the change as
a whole. Independent review happens separately via opencode's `/audit-pr`
after this PR is open. Do not read a green self-audit as a reviewed change.

## Issue #111 stated problem and impact

```
[x] voice_match updates transcript.segments and transcript.updated_at but not
    transcript.speaker_count — delivered, `transcript.speaker_count =
    count_distinct_speakers(new_segments)` at services/llm_jobs.py:754,
    placed between the segments write and `db.commit()` so both land in one
    transaction.
[x] "UI shows incorrect speaker count after voice match ... may show '3
    speakers' when voice_match reduced it to 2" — delivered and proven as a
    red-green pair. With the line removed the regression test fails
    `assert 3 == 1` (stale 3); with it restored it passes. The UI surface the
    issue describes is the detail-page Speakers stat tile at
    static/rack.js:5022, which renders `t.speaker_count` read-only.
[x] "Compare with rediarize which explicitly sets transcript.speaker_count
    after relabeling" — confirmed at services/llm_jobs.py:681
    (`transcript.speaker_count = speaker_count`); voice_match is now symmetric
    with it on this field.
[x] Proposed fix computes a recompute, not a decrement — kept. Confirmed
    necessary: matching is per segment, not per cluster, so the count can rise
    as well as fall (`new_segments[i] = {**seg, "speaker": matches[0]["name"]}`
    at services/llm_jobs.py:739 runs per segment independently).
```

## Promises made in investigation.md

```
[x] All 5 write paths that rewrite segments without re-diarizing now recompute
    `speaker_count`, not just the 1 the issue named — delivered:
    voice_match at services/llm_jobs.py:754; `update_transcript` PATCH at
    app.py:2092; `rename_transcript_speaker` at app.py:2315;
    `retag_transcript_segments` at app.py:2374; `undo_last_relabel` at
    app.py:2406. Verified by `grep -c count_distinct_speakers`: 5 hits in
    app.py (1 import + 4 call sites), 2 in services/llm_jobs.py (1 import +
    1 call site).
[x] One shared definition rather than 5 inlined copies —  delivered,
    `def count_distinct_speakers(segments) -> int` at services/relabel.py:20.
[x] The "Unknown" sentinel is excluded, so a recompute cannot report a higher
    count than the diarize path would for the same segments — delivered,
    `NON_SPEAKER_LABELS = frozenset({"unknown"})` at services/relabel.py:17,
    applied via `and name.casefold() not in NON_SPEAKER_LABELS` at
    services/relabel.py:34.
[x] Falsy and whitespace-only labels excluded — delivered, the walrus guard
    `if (name := (seg.get("speaker") or "").strip())` at services/relabel.py:33.
[x] Sites that already write speaker_count were left alone — confirmed
    unchanged: services/llm_jobs.py:681 (rediarize), app.py:1391-1393 (inline
    initial diarize), services/queue.py:601-607 (chunked finalize). `git diff`
    touches none of those lines.
[x] Serializer contract unchanged — confirmed `"speaker_count": t.speaker_count`
    still at app.py:380, and tests/test_serialize_transcript_contract.py:31
    still passes in the full run.
[x] No e2e selector churn — confirmed: `grep -rn "speaker_count\|speakers"
    tests/e2e/` returns nothing, and no UI text, control, label, or role
    changed (only a numeric value).
```

## Tests, with a mutation-check line each

Mutation protocol used: the body of `count_distinct_speakers`
(services/relabel.py:20) replaced by `return 0`, then by `return 1`, restored
by inverse edit after each. A test is only credited if at least one mutant
kills it.

```
[x] tests/test_relabel_speaker_count.py:7 test_empty_list_is_zero — mutation
    check: fails under `return 1`? yes
[x] tests/test_relabel_speaker_count.py:11 test_none_is_zero — mutation check:
    fails under `return 1`? yes
[x] tests/test_relabel_speaker_count.py:15 test_three_distinct_labels —
    mutation check: fails under both `return 0` and `return 1`? yes
[x] tests/test_relabel_speaker_count.py:24
    test_shared_name_across_segments_merges_to_one — mutation check: fails
    under `return 0`? yes
[x] tests/test_relabel_speaker_count.py:33 test_none_speaker_value_not_counted
    — mutation check: fails under `return 0`? yes
[x] tests/test_relabel_speaker_count.py:38
    test_empty_string_speaker_not_counted — mutation check: fails under
    `return 0`? yes
[x] tests/test_relabel_speaker_count.py:43
    test_segment_with_no_speaker_key_not_counted — mutation check: fails under
    `return 0`? yes
[x] tests/test_relabel_speaker_count.py:48 test_unknown_sentinel_excluded —
    mutation check: fails under `return 0`? yes
[x] tests/test_relabel_speaker_count.py:53
    test_unknown_sentinel_excluded_case_variants — mutation check: fails under
    `return 0`? yes
[x] tests/test_relabel_speaker_count.py:58 test_only_unknown_segments_is_zero
    — mutation check: fails under `return 1`? yes
[x] tests/test_relabel_speaker_count.py:63
    test_surrounding_whitespace_does_not_create_second_speaker — mutation
    check: fails under `return 0`? yes
[x] tests/test_voice_match_job.py:251
    test_voice_match_recomputes_speaker_count_on_merge — mutation check: fails
    under `return 0`? yes (passes under `return 1` because its expected value
    is 1; killed by the other mutant, so not vacuous). This is also the
    red-green regression test: verified failing `assert 3 == 1` with the
    services/llm_jobs.py:754 assignment removed, passing with it restored.
[x] tests/test_voice_match_job.py:288
    test_voice_match_no_match_leaves_speaker_count_matching_segments —
    mutation check: fails under both `return 0` and `return 1`? yes (expects
    2). Note this one is a no-op guard, not a red-green test: it passes
    against unfixed code too, by design, because its point is that the
    recompute does not disturb a run that relabeled nothing.
[x] tests/test_relabel_undo.py:79 test_rename_merge_drops_speaker_count —
    mutation check: fails under `return 0`? yes. Red-green verified: failed
    `assert 2 == 1` with the app.py:2315 assignment removed.
[x] tests/test_relabel_undo.py:93 test_retag_raises_speaker_count — mutation
    check: fails under both `return 0` and `return 1`? yes (expects 2)
[x] tests/test_relabel_undo.py:111 test_relabel_undo_restores_speaker_count —
    mutation check: fails under both mutants? yes (second assertion expects 2)
[x] tests/test_patch_segments_recomputes_speaker_count at
    tests/test_relabel_undo.py:131 — mutation check: fails under both mutants?
    yes (expects 3)
```

```
[x] FULL suite run before checking any test box, not just the touched files —
    `842 passed, 1 skipped, 22 deselected, 1 warning in 254.16s`, run twice
    (once backgrounded, exit 0; once in the foreground for the summary line).
    The 22 deselected are the `-m e2e` browser tier, not failures.
[x] New helper has its own tests rather than relying on incidental coverage —
    tests/test_relabel_speaker_count.py, 11 tests, exact `==` assertions
    throughout, no `in (...)`.
```

## Decisions this issue did not ask for, disclosed

```
[decision] Fixed 5 call sites, not the 1 the issue named — not specified by
  the issue, because all 5 rewrite segments without re-diarizing and none
  updated the count. Fixing only voice_match would have made
  `undo_last_relabel` (app.py:2406) a new stale path: undoing a voice match
  would restore old labels while leaving the recomputed count, which is the
  same bug inverted. Required by the repo's Complement Rule.
[decision] Excluded the literal "Unknown" from the count, case-insensitively —
  not specified by the issue, whose snippet filters only falsy values.
  Reason: `combine_with_transcript` writes "Unknown" into persisted segments
  (services/diarization.py:300) while the diarization services compute their
  own count pre-merge (services/diarization.py:129, :252, :420), so a
  falsy-only filter would report MORE speakers than rediarize does for the
  identical segment list. Side effect to be aware of: a real speaker whose
  name is literally "unknown" would not be counted.
[decision] Left `rediarize` and the two first-diarization paths on their
  existing pre-merge cluster count — not specified by the issue, because they
  are not broken (they do write the field) and changing them is outside #111.
  Consequence, disclosed rather than fixed: `speaker_count` still has two
  definitions in the codebase, so a stored pre-merge count can exceed the
  post-merge label count when a diarization cluster wins no transcript
  segment. The first relabel action on such a transcript will revise the
  displayed number downward. Detailed in wrong-directions.md with a
  recommended follow-up.
[decision] Did NOT fix the missing cancellation guard in voice_match that the
  Phase 1.5 (Fable) check surfaced — out of scope for #111 and deliberately
  not smuggled into this diff. `voice_match` has no in-loop cancel poll and no
  guard before `transcript.segments = new_segments`, unlike the voice_dump
  branch (services/llm_jobs.py:629-632) and the tagging branch (:531-533), so
  cancelling neither stops the work nor prevents the relabel. Recorded in
  wrong-directions.md as its own issue.
[decision] Applied the recompute unconditionally in voice_match rather than
  gating it on `if changed:` — not specified by the issue. A gated version
  would preserve today's value on a zero-match run, but it would also keep a
  count that disagrees with the segments. Unconditional keeps one invariant:
  after any of these 5 paths writes segments, the count matches those
  segments. tests/test_voice_match_job.py:288 pins the zero-match behavior.
[decision] Did not add a test for `job.result_json` on voice_match — the
  investigation found rediarize sets it and voice_match does not, but no
  consumer reads it, so it stays a cosmetic asymmetry, out of scope.
```

## Housekeeping

```
[x] Temporary verification edits restored by inverse edit only, never
    `git checkout`/`git stash`/`git restore` — confirmed: `git diff --numstat`
    back to exactly `13 1 app.py`, `6 0 services/llm_jobs.py`,
    `27 0 services/relabel.py` after every mutation and red-green cycle, and
    all 5 assignments re-confirmed present by grep afterward.
[x] One em dash introduced in a new test docstring was replaced with a colon
    at tests/test_voice_match_job.py:290 to match repo writing style. The
    many pre-existing em dashes elsewhere in these files were left alone.
[x] All four self-report files exist in
    .omo/runs/issue-111/worktree-issue-111-speaker-count/: investigation.md,
    self-audit.md, wrong-directions.md, token-usage.md.
```

## Post-audit addendum (independent review by GPT-5.6 Luna, verdict APPROVE)

```
[x] Should-fix: `services/relabel.py` could raise AttributeError on non-string
    speaker values from PATCH input — FIXED in commit db6e0fa. Reproduced
    first (`'int' object has no attribute 'strip'`), and found broader than
    reported: a non-dict entry in the segments array also raised
    (`'str' object has no attribute 'get'`). The helper is now total over
    arbitrary input via `isinstance(seg, dict)` and
    `isinstance(seg.get("speaker"), str)`. Junk is not coerced into a name,
    because `str(123)` would invent a speaker the user never labeled.
[x] Chose NOT to add validation/rejection at the PATCH endpoint, which was
    the reviewer's other suggested option — that would add new 400s to an
    endpoint whose contract this issue was not scoping. Making the helper
    total fixes the 500 without changing what the endpoint accepts.
[x] 6 new tests, all red-green verified against the pre-fix helper body
    (all 6 failed, then passed after the inverse edit restored the guards):
    tests/test_relabel_speaker_count.py `test_non_string_speaker_value_not_counted`,
    `test_bool_speaker_value_not_counted`, `test_container_speaker_value_not_counted`,
    `test_non_dict_segment_entry_not_counted`, `test_only_malformed_input_is_zero`,
    and the endpoint-level `test_patch_malformed_segments_does_not_500` in
    tests/test_relabel_undo.py.
[decision] The endpoint test covers only object entries with bad speaker
    values, not non-dict array entries — during red-green the non-dict case
    turned out to be rejected further down by the FTS triggers
    (`json_extract(value,'$.text')` over `json_each(NEW.segments)`,
    database/__init__.py:637) with `sqlite3.OperationalError: malformed JSON`.
    That is pre-existing and untouched by this change: such a request 500s
    either way, just from the DB layer instead of the helper. The helper's
    non-dict guard is kept as defense in depth and unit-tested directly.
[x] Nit, 842 vs 843: the run reported `842 passed, 1 skipped, 22 deselected`.
    843 is that same run counting the skip as a test. Moot now, the suite is
    `848 passed, 1 skipped, 22 deselected` with the 6 added tests.
[note] The reviewer reported "no self-report artifacts found, so 0/0 claims
    verified". Expected: `.omo/` is gitignored, so these four files are not
    in the PR or the repo and a PR-scoped reviewer cannot see them. Not a
    finding against the change; worth knowing that the honesty check ran
    blind to the self-report.
```

## Outcome

CI green (`tests pass`, 2m28s). PR #337 squash-merged as `e33192d`; issue #111
auto-closed. Worktree and branch removed after confirming all 23 new tests and
the fix are present on `origin/master`.
