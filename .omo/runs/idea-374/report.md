# /idea run report — idea-374

## Original idea
Dump Notes nav item should show a badge count of unread/pending items — mentioned in #322 as excluded but never got its own issue

## Filed issue(s)
- #374 — "Dump Notes nav badge should show unseen finalized-item count" — https://github.com/tito13kfm/whisperdeck/issues/374

## Phase 1 (challenge)
Conflict overridden. `VoiceDumpItem` has no processed/unprocessed concept — every row is already "finalized," and the existing Voice Notes badge it would mirror is a plain total count, not an unread count. User's reasoning: proceed anyway with the bigger scope ("Add a real unread/pending status field") rather than fall back to a plain total count, since "unprocessed" wouldn't mean anything without a real status field.

## Phase 2 (prior-art)
Found #286 (closed, shipped the nav item itself, explicitly deferred the badge) and #322 (open, claims the badge was "folded into #312" — inaccurate, #312 is an unrelated DELETE-route issue). No issue or PR implements a seen/unseen field or badge wiring; two independent searches (code + GitHub) confirmed the gap is real and unclaimed, so proceeded to file.
