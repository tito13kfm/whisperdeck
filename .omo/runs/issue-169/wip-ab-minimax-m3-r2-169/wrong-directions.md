# Wrong Directions — Issue #169 (minimax-m3-r2)

Things the issue text, AGENTS.md, the prior run's investigation, or
this run's assumptions got wrong, and the recommended fix for each.

## 1. `atlas` and `quick`/`writing`/`unspecified-low` are local, not cloud

AGENTS.md line 127 lists these as "OpenRouter-billed" (cloud, no cap).
The current `~/.config/opencode/oh-my-openagent.json` (verified this run)
maps ALL FOUR to `lemonade/*` model prefixes — local, subject to the
2-agent cap. This is a real, checkable error in AGENTS.md's own
agent-cap table, not a one-off.

**Impact this run:** would have been 0 — I did the implementation
myself without delegating to a subagent, so the 2-cap didn't matter
here. But the next variant that tries to dispatch `quick`/`unspecified-low`
in parallel to `explore` would silently thrash the GPU.

**Fix for AGENTS.md:** read the live config before citing agent local/
cloud status. The doc should say "verify against `~/.config/opencode/
oh-my-openagent.json` for the current mapping" rather than maintain a
drift-prone list.

## 2. The `summarize()` defensive branch isn't reachable from the user-facing path

I added a `voice_note` branch to `transcription_service.summarize()` as
a "defense in depth" measure. But the `/api/transcripts/{id}/summarize`
route now rejects `voice_note` with 400 BEFORE the service is called,
and the only other path to `summarize()` is the LlmJob "summary"
dispatch in `run_llm_job` — which itself takes the kind from
`job.kind`, not from the transcript. A user can't enqueue a "summary"
job against a voice_note transcript without going through the route
(there's no other enqueue path for kind="summary"). So the defensive
branch is unreachable in practice.

**Impact:** low. The defensive branch is harmless and self-documents
the intent. But the cost is real: 30 lines of code and a test scenario
that can never fire. If I were running this again, I'd skip the
defensive branch and trust the route guard.

**Fix for future:** when adding a route guard for a new kind, don't
also duplicate the guard in the service layer. The route is the
entry point; the service trusts its inputs.

## 3. `tog-mode` binary visual class is reused for 3-way state

The CSS `.tog` class is binary (on/off paddle position). I reused it
for the 3-way mode toggle by collapsing the state into a boolean
(`singleSpeaker = mode === 'dictation' || mode === 'voice_note'`) and
relying on the `vfd-mode` text label to distinguish the three values.
Visually, the user sees a binary paddle that doesn't change between
`dictation` and `voice_note` — only the text label updates.

**Impact:** the user can read the current mode (text label) but the
paddle position is misleading for `voice_note` (it looks like
`dictation`). This is a minor UX paper cut.

**Fix for follow-up issue:** a true 3-position `.tog-3` class with a
paddle that snaps to 0%, 50%, or 100% left, with three labels under
the paddle. The current implementation is a stopgap that ships the
3-way value with the existing binary visual.

## 4. Prior runs' investigations had small mistakes I almost copied

I read both `wip-ab-minimax-m3-169/investigation.md` and
`wip-ab-deepseek-pro-r2-169/investigation.md` before starting, to
avoid duplicating work. The `deepseek-pro-r2` investigation claimed
the `/format/{target}` route "rejects non-dictation, including
voice_note" via the existing 400 check — but the existing 400 check
only checks `t.kind != "dictation"`, so a voice_note transcript would
PASS the check and try to enqueue a `format_markdown` job. The
`deepseek-pro-r2` run did update the route to reject voice_note, but
their investigation didn't enumerate the 400 message wording. I caught
this when I went to write the per-kind message — the existing wording
("Reformatting is only available for dictation transcripts") would have
been misleading for a voice_note rejection.

**Impact:** the `deepseek-pro-r2` code DID update the route, so the
user-facing behavior was correct. But the investigation's "covered"
note was technically inaccurate. The two issues' investigations
disagreed on whether voice_note should be its own rejection or fall
under the dictation 400. I chose the per-kind wording because the
issue says "a place for these notes to live" — implying voice_note is
its own concept, not a dictation variant.

**Fix for this run:** the per-kind rejection at app.py:1962-1964 is
the right call. No change needed.

## 5. The `tog-diarize.on` lock needed re-thinking

I extended the `singleSpeaker` check to also lock the diarize toggle
when `mode === 'voice_note'`. This was right for the rail UI
("Speakers" is N/A for voice_note, same as dictation). But I didn't
check whether the backend `_run_transcription_pipeline` would behave
correctly if a future caller passed `kind='voice_note'` AND
`diarize=True` from a different endpoint. Looking at the code, the
backend force-off at app.py:954 covers this — but the COMMENT
justifying the backend force should also mention voice_note. It
currently says "Dictation transcripts are always single-speaker by
definition" — needs an update for "voice_note" too.

**Impact:** none in this run (the comment update is the only thing).
The behavior is correct.

**Fix for follow-up:** update the comment at app.py:933-937 to
mention voice_note alongside dictation.
