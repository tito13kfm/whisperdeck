# Brainstorm: semantic / RAG search over transcripts

> Companion to `docs/plans/01-semantic-rag-search.md` (the draft plan). This document widens the
> solution space and pressure-tests the draft's choices before anyone commits to it. It does not
> replace the plan, it argues with it.

## User-intent framing first

**What problem does this actually solve?** Today, `services/search.py`'s `search_transcripts()`
and `search_transcripts_snippets()` require the query's literal words (or close variants, since
FTS5 does prefix/stem matching but not synonyms) to appear in the transcript. The real user pain
is meeting recall: someone remembers *that* a decision was made, not the exact phrase used to make
it. "What did we decide about the vendor contract" should find a meeting that only ever says
"renewing with Acme Corp."

**What does "good" retrieval look like for meeting speech specifically?**
- Find the right meeting *and* the right moment inside it (jump to timestamp), not just "this
  transcript contains something related."
- Favor a small number of dead-on relevant chunks over a wide, vaguely-related set. This matters
  concretely: `services/assistant.py`'s `execute_plan()` summarize step truncates context at
  60,000 characters. Noisy semantic recall that pads the result list with marginal chunks pushes
  genuinely relevant segments past that truncation point before the LLM ever sees them. Precision
  matters more than recall here, the opposite of a lot of generic RAG advice.
- Preserve speaker attribution ("what did Sandeep say") since that's a common query shape for
  meeting transcripts specifically, unlike generic document search.

**Assumptions the draft plan is making silently, worth surfacing:**
1. That embeddings are worth it *at all* for a given user's corpus. If someone has a few dozen
   short transcripts, FTS5 plus the assistant's own paraphrase-tolerant summarization may already
   cover most conceptual gaps. Value scales with corpus size and how often real queries use
   different words than the transcript. Nobody has measured how often today's `search_transcripts`
   returns nothing for a real assistant query, that number would justify (or not) the investment.
2. That chunk-level content embeddings are the first thing worth trying, when `TranscriptTag`
   (issue #171, already shipped) gives a cheap, controlled-vocabulary alternative: embedding or
   matching against a short tag list is a much smaller, more tractable problem than free-text
   embedding, and the tagging pipeline already exists. Worth at least ruling in or out explicitly
   rather than skipping straight to full chunk embeddings.
3. That a small 384-dim local model's retrieval quality is "good enough" without ever having
   checked it against real transcripts. There's no eval step in the draft plan before locking in
   chunk size and model choice.
4. That per-chunk storage doesn't need explicit user scoping. The proposed `TranscriptChunk` schema
   has no `user_id` column, relying on a join through `transcript_id -> Transcript.user_id` for
   isolation. That's fine functionally (every other per-user query in this codebase joins the same
   way) but every semantic query needs that join done correctly, worth calling out so it's not
   missed under time pressure.

---

## Decision 1: local embedding generation library

| Option | Local-first fit | Windows install | Quality/selection | Notes |
|---|---|---|---|---|
| **fastembed** (ONNX Runtime, Qdrant) | High, no torch | Low pain: onnxruntime + tokenizers (rust, prebuilt wheels) + huggingface_hub, all have Windows wheels | Good small models (bge-small-en, ~130MB, 384-dim) | First use still downloads the model over network (same pattern Moonshine/Whisper already have, not a new problem) |
| **sentence-transformers** | Breaks the pattern: pulls full `torch` + `transformers` (500MB-1GB), same dependency this codebase currently keeps optional (`requirements-diarization.txt` only, for pyannote) | Torch on Windows is fine but heavy | Larger model zoo, more mature tooling | Only "free" for users who already installed the diarization extra (torch already present), not worth a special-cased second code path just for that overlap |
| **API embeddings provider** (OpenAI `text-embedding-3-small`, etc.) | Lowest: sends transcript content to a third party by default, a privacy-sensitive move for a local-first meeting-transcript app | None (no local install) | High, and it's a recurring cost tied to chunk count, not transcript count | Fine as opt-in, wrong as default |
| Reuse `services/voice_id.py` backends (speechbrain/pyannote/librosa MFCC) | N/A | N/A | N/A | Not applicable, those are audio-embedding models, wrong modality entirely. Mentioned only to rule out. |

**Recommendation:** fastembed as the local default, matching the plan. One addition the plan
doesn't flag: fastembed's small English model may be a poor fit if a user transcribes non-English
meetings (`Transcript.language` supports `auto`). A multilingual small model (e.g.
multilingual-e5-small) is also available through fastembed but is larger and slower. Decide this
explicitly rather than defaulting silently to an English-only model for a multilingual-capable app.

---

## Decision 2: vector storage and search

| Option | Complexity | New binary deps | Breaks down at |
|---|---|---|---|
| **JSON column + brute-force cosine** (mirrors `VoiceProfile`/`VoiceClip` + `VoiceIdService._cosine_similarity()`) | Lowest, matches existing idiom exactly | None | Not "thousands of chunks", the real cost is the per-query `SELECT` + `json.loads()` deserialization pass over every stored chunk, which scales linearly with row count. The cosine math itself (numpy matmul) stays sub-millisecond well past tens of thousands of rows. The realistic break point is a very heavy multi-year user with tens of thousands of chunks, where the deserialization pass becomes seconds, not a typical personal/team archive. |
| **sqlite-vec** (maintained successor to sqlite-vss) | Medium: loadable extension, `enable_load_extension(True)`, a new "extension failed to load" failure mode that doesn't exist today | One compiled binary per platform (Windows amd64 wheel exists) | Scales much further, avoids full deserialization | Real payoff only once the JSON approach is a measured bottleneck |
| **Separate index file** (FAISS, hnswlib, memmapped numpy) | Highest: reintroduces a dual-write consistency problem this app has deliberately avoided everywhere else (SQLite is the single source of truth) | Yes, plus a rebuild-on-corruption story | Best raw scale, worst operational fit |

**Recommendation:** brute-force cosine now, exactly as the plan proposes, but be explicit about
*when* to revisit: when a manual timing check during backfill or search shows real lag, not a
speculative corpus-size threshold. Keep the lookup behind a swappable function (plan already says
this) so sqlite-vec is a drop-in later. Skip the separate-index-file option outright, the
dual-write risk isn't worth it while sqlite-vec exists as a lower-risk faster path.

---

## Decision 3: chunking strategy

| Option | Retrieval quality for meeting speech | Notes |
|---|---|---|
| **Per-segment** | Poor: many segments are a single short utterance ("yeah", "okay so"), diluting retrieval with near-content-free vectors. Also multiplies embedding job count a lot (a one-hour meeting can have hundreds of segments). | Rejected in the plan already, correctly |
| **Merged windows** (plan's choice, ~150-300 words, small overlap) | Good middle ground, preserves natural segment/timing boundaries | The word-count target is a real tunable, not a settled number. Too small loses cross-turn context (a decision's rationale can span several exchanges); too large blurs multiple topics into one vector in a fast-moving meeting. Overlap helps at topic boundaries but creates near-duplicate chunks that can crowd out a genuinely different-topic chunk from elsewhere in the transcript, in a top-K result. |
| **Whole-transcript** | Worst granularity: answers "which meeting" not "what part of the meeting," loses jump-to-timestamp | Correctly rejected in the plan |

**Recommendation:** merged windows, but add a time-gap cap alongside the word-count target: don't
merge across a multi-minute silence or gap (someone stepped away, a meeting restarted after a
break). A word-count-only window can otherwise splice two unrelated conversational moments into
one chunk just because the transcript happens not to have many words in between. Validate the
actual size choice against a handful of real transcripts before locking it in, this is exactly the
kind of thing that looks fine in the abstract and wrong on real meeting audio.

---

## Decision 4: hybrid fusion

| Option | Wins when | Loses when |
|---|---|---|
| **Keyword-only** (status quo) | Exact term, proper noun, ID, acronym, or number lookup; zero tolerance for false positives | Any conceptual paraphrase |
| **Semantic-only** | Conceptual/paraphrased queries | Exact proper nouns, IDs, dates, short unique tokens (embeddings are notoriously weak here, "Acme Corp" or a ticket number blurs into generic vector space) |
| **RRF** (plan's choice) | No score-scale mismatch to solve (BM25 rank and cosine similarity aren't comparable magnitudes), standard choice, needs no tuning data | Coarse: only uses rank position, not magnitude, so an overwhelming exact keyword match sitting at rank 3 can get diluted by several mediocre semantic hits occupying the top ranks. |
| **Weighted score normalization** | More tunable (a knob to bias keyword vs. semantic) | Requires a normalization scheme and a weight, both need real query data to tune well, otherwise they're guesses baked in from whatever informal testing happened during development |

**Recommendation:** RRF as the plan proposes. One addition: since semantic search is specifically
weak at exact proper nouns/IDs, do a quick manual check with a handful of "find this exact term"
queries once hybrid is wired up, to make sure RRF isn't burying a strong exact match under several
weak semantic ones. That's a five-minute sanity check, not a tuning project, but skipping it risks
a regression on exactly the query type FTS5 already handles well today.

**Sibling-entry-point risk (per this repo's own known failure mode):** `services/search.py`
already has *two* different-shaped functions serving *two* different callers:
`search_transcripts()` (returns `matching_segments`, feeds the assistant's `search` action) and
`search_transcripts_snippets()` (returns HTML snippets, feeds `/api/search` and the bank search
UI). A hybrid patch that only touches one of them leaves the other keyword-only with no visible
signal that it happened, exactly the "sibling entry point missed" pattern that's bitten this repo
before (see `AGENTS.md`'s Complement Rule). Either unify the two into one hybrid-capable function
with two output shapes, or explicitly decide (and document) that only one of the two gets hybrid
search first and the other stays keyword-only on purpose.

---

## Decision 5: staleness (re-embed policy)

Correction (`LlmJob(kind="correction")`) changes `corrected_text`, which is exactly the kind of
change that should trigger re-embedding since it changes chunk-worthy content. `rediarize` and
`voice_match` change `segments[].speaker` labels; whether that matters depends on whether chunk
text embeds speaker names inline (open question, not yet decided in the schema draft).

| Option | Cost | Correctness |
|---|---|---|
| **Re-embed the whole transcript's chunks on any relevant change** | Some waste (a rediarize-only relabel might not have changed embeddable text at all) | Simple, always correct |
| **Per-chunk content hash, re-embed only changed chunks** (plan's own open question) | Cheaper at scale | Needs real chunk-alignment logic: correction can shift word counts and chunk boundaries, so "did this chunk's text change" isn't a trivial diff against the old chunk set |
| **Manual-only, no auto re-embed** | Cheapest | Silently stale until a user notices semantic search missing something they just corrected. No automatic signal that drift happened, worth flagging under Risks below. |

**Recommendation:** whole-transcript re-embed triggered only by `correction` job completion for the
MVP (not `rediarize`/`voice_match`, unless chunk text is later found to need inline speaker names).
Content-hash partial re-embed is real future work, not worth building before there's a measured
cost problem, this mirrors the same "simple first, revisit if scale forces it" posture as the
vector storage decision.

---

## Decision 6: backfill (existing corpus)

`database/__init__.py`'s `populate_fts()` is the existing precedent, called synchronously inside
`init_db()` on every startup. It's cheap (string concatenation via an anti-join, recently optimized
in PR #205 to avoid N+1 connections) so blocking startup on it is fine. Embedding generation is a
fundamentally different cost profile: each chunk needs an actual model inference call
(milliseconds to tens of milliseconds even on CPU with ONNX), and a corpus of thousands of chunks
turns that into real, user-visible startup delay if done the same synchronous way.

| Option | Startup impact | Notes |
|---|---|---|
| **Blocking startup migration, `populate_fts()`-style** | Bad: minutes-long delay possible on an existing large corpus | Wrong cost profile for this operation, explicitly what the ask says to avoid |
| **Enqueue one `embed` LlmJob per transcript, let the worker pool chew through it** (plan's Phase 2) | None, reuses existing queue infrastructure and caps | A large backfill sits in the queue for a while; needs a visible progress indicator so it doesn't look broken |
| **Lazy backfill on first search near a transcript** | None | Confusing UX: search silently underperforms for anything not yet organically embedded, no way to tell a user why a search "didn't find" something that exists |

**Recommendation:** manual "Rebuild index" trigger as the plan proposes, plus a *bounded* automatic
backfill at startup (most recent N completed transcripts only, not the entire history at once).
An unbounded startup backfill risks queuing thousands of jobs that compete with the CPU/IO pool for
resources the user is actively waiting on (a correction or summary they just requested). Older
history backfills only when the user explicitly asks for it.

---

## Decision 7: scope of the query surface

| Option | Blast radius | Value |
|---|---|---|
| **Assistant only** | Small: one new call site, no `/api/search` change, no `rack.js` UI work | High: natural-language questions are exactly where semantic recall pays off most, and it's the surface the plan's own motivating example ("what did we decide about the vendor contract renewal") targets |
| **Both assistant and main search UI at once** (plan's current phasing: Phase 4 wires `/api/search`, Phase 5 wires the assistant) | Larger: two call sites, provenance UI in `rack.js`, a feature flag gating two places instead of one | Also valuable, but doubles the surface to get right before anyone has confirmed the semantic function itself works well on real transcripts |
| **Main search UI only** | N/A | Doesn't make sense as a starting point, the assistant's free-text framing is the more natural fit. Mentioned only to rule out. |

**Recommendation:** land the assistant slice first as the actual MVP, and treat `/api/search` +
bank-search-UI wiring as an explicit next phase, gated on the assistant slice showing a real
improvement on a handful of real queries. This is a re-ordering suggestion relative to the draft
plan's Phase 4/5 split, not a rejection of doing both eventually.

---

## Risks / failure modes and how to detect them

- **Bad recall.** Detect with a small fixed set of 5-10 real query/expected-transcript pairs,
  checked manually after any model or chunk-size change. In the wild this shows up as users saying
  semantic results feel arbitrary or worse than keyword-only.
- **Embedding drift on a backend/model swap.** The `embedding_model` provenance column (mirroring
  `VoiceClip`) already tracks which backend produced a vector. The plan should explicitly decide
  what happens to old vectors when the backend changes: exclude them from cosine top-K if
  `embedding_model` doesn't match the current query's model (same pattern
  `VoiceIdentificationService.identify()` already uses, skipping non-matching profiles), so a
  backend swap fails safe into keyword-only results rather than silently comparing incompatible
  vectors.
- **Cost surprise on an API embedding backend.** Auto re-embed after every correction completion
  means embedding call volume scales with chunk count, not transcript count (a two-hour meeting can
  be 20+ chunks). Route this through the same `services/cost.py`/`services/pricing.py` tracking
  already used for chat completions, and keep API embedding opt-in only, off by default.
- **Coverage gaps from chunk-boundary drift.** If per-chunk content hashing lands later, a bug that
  shifts chunk boundaries after correction without correctly re-embedding could leave a transcript
  with inconsistent, non-tiling chunk coverage, missing or duplicated spans nobody notices until a
  search for something in the gap comes up empty. Per this repo's own PR #205 lesson (vacuous
  tests), a coverage check should assert chunks actually cover the transcript's duration, not just
  that some chunks exist.
- **Job pool starvation.** `embed` will land in either `CPU_KINDS` (cap 1) or `IO_KINDS` (cap 2)
  depending on backend. A backfill burst competing with `rediarize`/`voice_match` (if CPU-bound) or
  `correction`/`summary` (if API-bound) for the same small pool cap can make unrelated jobs wait
  behind a large backfill. Existing Queue screen visibility helps surface this, but the pool
  placement decision should be made deliberately, not left as an afterthought.

---

## MVP slice vs. later phases

**MVP (smallest version that proves value):**
- Local embedding backend only (fastembed), no API opt-in yet.
- Merged-window chunking, backfill via a manual "Rebuild index" trigger only, no auto-enqueue on
  transcription/correction completion yet (defers the staleness decision).
- Semantic top-K wired into the assistant's `search` action only, not `/api/search`.
- Skip RRF for this slice: run keyword and semantic separately and merge naively (e.g. keyword
  results first, semantic appended), deferring the fusion math until there's evidence hybrid
  actually beats either alone for real assistant queries.
- Manual eval: a handful of real "what did we decide/discuss" queries against real transcripts,
  eyeballed against keyword-only baseline.

**Later phases (largely the plan's own ordering, now sequenced after the MVP validates value):**
- Auto-enqueue `embed` after transcription/correction completes, staleness policy decided.
- RRF hybrid fusion, wired into `/api/search` and the bank search UI with match-provenance display.
- API embedding provider as an opt-in alternative.
- `sqlite-vec` only if brute-force cosine becomes a measured bottleneck.

---

## Decisions needed from the human

1. **Query surface order:** ship semantic search into the assistant first and validate before
   touching `/api/search` and the bank search UI, or build both at once as the draft plan currently
   phases it?
2. **API embedding provider on day one or not at all initially:** is sending transcript content to
   a third-party embeddings API acceptable as an opt-in from the start, or should that wait until
   local-only has proven itself?
3. **Re-embed trigger for MVP:** auto re-embed the whole transcript after every correction
   completion (simple, a bit wasteful), or hold off on any auto-trigger until content-hash partial
   re-embedding is designed (more correct, more upfront work, and no auto-embed at all for the MVP
   window)?
4. **Is there a way to size the actual problem first?** Before committing further design and build
   time, is there any way to check how often today's `search_transcripts`/assistant search comes up
   empty on a real query, to confirm the vocabulary-mismatch problem is common enough to justify
   this scope, versus already narrow enough that FTS5 plus the assistant's own summarization covers
   most cases?
