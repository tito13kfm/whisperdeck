# Semantic / RAG search over transcripts

> One-line status: Draft plan. Idea inspired by Blinko (github.com/blinkospace/blinko), concept only, no code copied.

## Motivation

Search today (`services/search.py`) is FTS5 keyword matching: `search_transcripts()` and
`search_transcripts_snippets()` both do an FTS5 `MATCH` against `transcripts_fts` and require the
query terms to appear more or less literally. That's fast and exact but misses conceptual queries
("what did we decide about the vendor contract renewal" won't hit a transcript that only ever says
"renewing with Acme Corp") and misses paraphrases, typos-of-meaning, or queries phrased differently
than the speaker's words. The assistant (`services/assistant.py`) inherits this limitation directly:
its `search` action calls `search_transcripts()`, so any conceptual ambiguity in a natural-language
request degrades the same way.

Adding embedding-based semantic search closes that gap without throwing away FTS5: the two are
complementary (exact terms vs. concepts), so the right end state is hybrid retrieval, not a
replacement.

## What Blinko does (attribution)

Blinko (self-hosted note app) pairs its full-text (BM25-style) index with vector embeddings of note
content, generated via a configurable embedding provider (local via Ollama or a hosted API), and
blends the two result sets for a single "smart search." The idea worth borrowing is narrow:
**generate embeddings for note/content chunks in the background, store them alongside the existing
keyword index, and merge keyword + vector results into one ranked list** rather than forcing the
user to pick a search mode. We borrow that shape only: the concrete WhisperDeck implementation
below (SQLite storage, job queue, chunking) is independent and follows this codebase's existing
patterns, not Blinko's code.

## Proposed approach

1. **Reuse the embedding-storage pattern already in this codebase.** `VoiceProfile`/`VoiceClip` in
   `database/__init__.py` already store an embedding as a JSON list of floats plus an
   `embedding_model` string tracking which backend produced it, and `services/voice_id.py`
   (`VoiceIdService._detect_backend()`, `_extract_embedding()`, `_cosine_similarity()`) already
   implements backend auto-detection with a graceful local fallback and brute-force cosine
   similarity in Python/numpy. Text embeddings for search should follow the exact same shape: a new
   table with a JSON embedding column, an `embedding_model` column for backend provenance, and
   plain numpy cosine similarity for lookup, with no new vector-search infrastructure needed at
   this app's scale (a personal/team transcript archive, not a web-scale corpus).

2. **Chunk transcripts, don't embed whole transcripts.** A single embedding per transcript is too
   coarse (a two-hour meeting covers many topics) and a single embedding per FTS segment is too
   fine (segments can be a few words). Group consecutive entries from `Transcript.segments` into
   chunks of roughly 150-300 words (sliding window, small overlap so a topic boundary that falls
   mid-chunk is still captured by the neighboring chunk), keeping the chunk's start/end time range
   and segment index span so a match can jump back to the right spot in the transcript.

3. **Generate embeddings as a background LlmJob**, mirroring `services/llm_jobs.py`'s existing
   `rediarize`/`voice_match` CPU-bound jobs: add an `embed` kind, dispatch it from `run_llm_job()`,
   auto-enqueue it after transcription completes and after correction completes (same shape as
   `enqueue_auto_tagging`/`enqueue_auto_correction`), and expose a manual "rebuild index" action for
   backfilling existing transcripts.

4. **Default to a local embedding model**, keeping with this app's local-first stance (builtin
   Whisper, Moonshine, librosa MFCC voice-embedding fallback are all zero-setup-by-default). Offer
   an API embedding provider as an opt-in alternative through the same `provider_configs` /
   `resolve_provider_key` machinery `services/settings.py` already has for chat providers.

5. **Hybrid search via Reciprocal Rank Fusion (RRF).** Run the existing FTS5 query and a new
   semantic (cosine top-K) query, fuse their rankings with RRF (`score = sum(1 / (k + rank))`
   across whichever lists a result appears in, `k` a small constant like 60) rather than trying to
   normalize BM25 rank against cosine similarity, which live on unrelated scales. This gives one
   blended ranked list for both `/api/search` and the assistant's search step.

6. **Teach the assistant to use hybrid search**, since `services/assistant.py`'s `search` action
   currently only calls `search_transcripts()` (exact-term matching against segments). Once hybrid
   search exists, the assistant's search step should call it directly: conceptual assistant
   queries ("what did we discuss about hiring") are exactly the case semantic search helps most.

## Code touchpoints (files + symbols, no line numbers)

- `services/search.py`: `search_transcripts()`, `search_transcripts_snippets()`,
  `_sanitize_fts5_query()`. Add a new semantic/hybrid search function here (or a sibling module,
  see below) that composes with the existing FTS5 query rather than replacing it.
- `database/__init__.py`: `Transcript` model (source of chunk text and `segments`),
  `VoiceProfile`/`VoiceClip` (embedding-storage pattern to mirror), `init_db()`, `migrate_schema()`,
  `populate_fts()` (precedent for a background backfill pass over existing rows; a
  `populate_embeddings()`-style helper would follow the same shape for chunk backfill).
- `services/voice_id.py`: `VoiceIdService._detect_backend()`, `_extract_embedding()`,
  `_cosine_similarity()`. Pattern to mirror for a new `services/embeddings.py` (backend
  auto-detection, embedding-model provenance tracking, cosine similarity helper).
- `services/llm_jobs.py`: `VALID_KINDS`, `IO_KINDS`/`CPU_KINDS`, `AUTO_RETRY_KINDS`,
  `run_llm_job()`, `llm_worker_tick()`, `enqueue_auto_tagging()`/`enqueue_auto_correction()`. The
  pattern for a new `embed` job kind and its auto-enqueue hook.
- `services/assistant.py`: `_SUPPORTED_ACTIONS`, `interpret_request()`, `execute_plan()`'s
  `search` branch. Swap in (or add alongside) the new hybrid search call.
- `services/settings.py`: `KEYLESS_PROVIDERS`, `resolve_provider_key()`,
  `get_user_settings()`/`update_user_settings()`. Add embedding-backend settings the same way
  correction/format providers are configured today.
- `app.py`: `search_transcripts_endpoint()` (`/api/search`) and the assistant execution endpoint
  that calls `execute_plan()`. Wire in the hybrid path, likely behind a settings flag so it can be
  toggled off if the local embedding backend isn't available.
- `static/rack.js`: Tape Library bank search UI (`bankSearchResults`, `#bank-search` input,
  `renderBankSearchResults`-equivalent block, `#bank-search-results` container). Surface a way to
  see which hits were semantic vs. keyword matches, similar to how `match_source` already
  distinguishes title/full_text/corrected_text/segment_text.
- `requirements.txt`: new optional dependency for local embeddings (see Research notes).

## Data model / schema changes

New table, `TranscriptChunk` (name tentative), added the same way `VoiceClip` was added: a plain
SQLAlchemy model plus `migrate_schema()`/`create_all()`, no separate migration framework in this
codebase:

- `id` (PK)
- `transcript_id` (FK to `transcripts.id`, `ondelete=CASCADE`)
- `chunk_index` (int, ordering within the transcript)
- `segment_start_index` / `segment_end_index` (int, span into `Transcript.segments`)
- `start_time` / `end_time` (float, seconds, for jump-to-timestamp on click, same fields
  `matching_segments` already returns)
- `text` (the chunked text that was embedded, preferring `corrected_text`-derived text when
  available, matching the existing `match_source` precedence of corrected over raw)
- `embedding` (JSON list of floats, mirrors `VoiceClip.embedding`)
- `embedding_model` (string, mirrors `VoiceClip.embedding_model`; tracks which backend produced
  this vector so a backend switch doesn't silently compare incompatible vectors, same guard
  `VoiceIdService._ensure_clip_compatible()` already implements for voice embeddings)
- `created_at`

`LlmJob.kind` gains `"embed"`, added to `VALID_KINDS`. Whether it lands in `CPU_KINDS` or
`IO_KINDS` depends on the chosen backend (local model is CPU-bound like `voice_match`; API provider
is network-bound like `correction`), so the plan should keep this backend-dependent rather than
hardcoding one pool.

`services/settings.py`'s per-user settings JSON gains: `embedding_backend`
(`local` | `<api-provider-name>` | `disabled`), `embedding_model`, `semantic_search_enabled` (bool).

## Research notes

- **Local embedding library choice: fastembed over sentence-transformers.** `sentence-transformers`
  pulls in `torch` as a hard dependency, a large install this codebase currently avoids by default
  (torch only shows up via the optional `requirements-diarization.txt` extra for pyannote). `fastembed`
  (Qdrant's library, ONNX Runtime-based) provides comparable small models (e.g. BAAI/bge-small-en,
  ~130MB, 384-dim) without a torch dependency, matching the "zero-setup local default" bar that
  `moonshine-voice` and the librosa MFCC fallback already set. Recommend a small install/benchmark
  spike on Windows before committing (this dev environment is win32; ONNX Runtime wheels are
  generally fine on Windows, but worth a five-minute confirmation before it's a hard dependency).
- **API embeddings as an opt-in, not the default.** OpenAI's `text-embedding-3-small` is cheap
  (roughly $0.02 per million tokens) and low-latency, but it requires a key and a network round
  trip per chunk, the same tradeoff shape `services/cost.py`/`services/pricing.py` already track for
  chat completions. Slot it in as another provider option, gated the same way `KEYLESS_PROVIDERS`
  gates chat providers today.
- **Vector storage: brute-force cosine over JSON, not sqlite-vec/sqlite-vss.** `sqlite-vec` (the
  actively maintained successor to `sqlite-vss`) gives real ANN search via a loadable SQLite
  extension, but adds a platform-specific binary dependency and operational complexity. At this
  app's expected scale (a personal or small-team transcript archive, hundreds to low thousands of
  chunks, not millions), a numpy-vectorized brute-force cosine similarity pass, exactly like
  `VoiceIdService._cosine_similarity()` already does for voice matching, is simpler, has zero new
  binary deps, and is fast enough. Worth reconsidering only if corpus size becomes a real
  bottleneck; design the lookup as a swappable function so that door stays open.
- **Chunking: segment-window merge, not fixed-size sliding window over raw characters.**
  Transcripts already carry natural segment boundaries (speaker turns, timing) via
  `Transcript.segments`; merging consecutive segments up to a word-count target preserves those
  boundaries and gives chunks that map cleanly back to a timestamp range, which raw
  character-sliding-window chunking would lose.
- **Hybrid fusion: RRF over score normalization.** BM25-style FTS5 `rank` and cosine similarity are
  on incomparable scales, so blending them by weighted score requires ad hoc normalization tuning.
  RRF sidesteps that by only using each result's *rank* in each list, which is why it's the standard
  choice for hybrid keyword+vector search elsewhere (Elasticsearch's hybrid retriever, etc.) and is
  a good fit here given the "not exhaustive" scope of this plan.
- **Async job placement.** `services/llm_jobs.py` already separates an I/O pool (API-bound:
  correction, summary, tagging, ...) from a CPU pool (local compute: rediarize, voice_match) capped
  independently so one doesn't starve the other. Embedding generation should join whichever pool
  matches the chosen backend rather than getting its own pool, consistent with the existing "two
  pools, kinds partition exactly" invariant (`test_io_cpu_pools_partition_valid_kinds`-style test in
  `services/llm_jobs.py`'s test suite).

## Open questions

- Re-embed policy after correction: does a completed `correction` LlmJob invalidate and re-trigger
  `embed` for that transcript's chunks (since `corrected_text` changed), or does staleness tracking
  need a per-chunk content hash to avoid needless re-embedding of unchanged text?
- Should `embed` be one job per transcript (embed all chunks) or should chunk-level granularity
  allow partial re-embedding after a correction only touches part of a transcript?
- Should semantic search be blended into every `/api/search` call unconditionally, or gated behind
  `semantic_search_enabled` so a user without an embedding backend configured sees identical
  behavior to today?
- Does the assistant's `search` action call hybrid search unconditionally, or does
  `interpret_request()`'s LLM plan get a new explicit action (e.g. `semantic_search`) so the
  planner can choose keyword-only vs. hybrid based on how the request reads?
- What happens to a transcript's chunks/embeddings on delete or retranscribe
  (`source_transcript_id` chains)? Cascade delete via FK is the obvious default, but retranscribe's
  relationship to a prior transcript's embeddings needs a decision (treat as unrelated new
  transcript, most likely).
- Final call on fastembed vs. an alternative ONNX-based library needs the Windows install spike
  mentioned in Research notes before locking it into `requirements.txt`.

## Rough phasing / checklist

**Phase 0: Spike**
- [ ] Confirm a local ONNX-based embedding library (fastembed or equivalent) installs cleanly on
      Windows without a torch dependency; benchmark embed time for a typical chunk count per
      transcript.

**Phase 1: Data model + chunking**
- [ ] Add `TranscriptChunk` model to `database/__init__.py`, wire into `migrate_schema()`/`create_all()`.
- [ ] Write the segment-window chunking function (merge consecutive `Transcript.segments` entries
      to a word-count target with small overlap; handle edge cases: no segments, one giant
      segment, empty transcript).

**Phase 2: Embedding generation**
- [ ] Add `services/embeddings.py` mirroring `services/voice_id.py`'s backend-detection /
      provenance-tracking / cosine-similarity pattern.
- [ ] Add `embed` to `LlmJob.VALID_KINDS` in `services/llm_jobs.py`, implement its branch in
      `run_llm_job()`, place it in `CPU_KINDS` or `IO_KINDS` per chosen default backend.
- [ ] Auto-enqueue `embed` after transcription completes and after correction completes, following
      `enqueue_auto_tagging()`/`enqueue_auto_correction()`'s shape.
- [ ] Add a manual "rebuild index" trigger for backfilling existing transcripts (mirrors
      `populate_fts()`'s backfill role for the FTS5 index).
- [ ] Add `embedding_backend` / `embedding_model` / `semantic_search_enabled` to
      `services/settings.py`'s user settings.

**Phase 3: Semantic query path**
- [ ] Add a semantic top-K lookup function (embed the query, cosine-rank stored chunk embeddings)
      in `services/search.py` or a sibling module.

**Phase 4: Hybrid fusion**
- [ ] Implement RRF fusion combining FTS5 rank and semantic rank into one ranked result list.
- [ ] Wire the fused result into `/api/search` (`app.py`'s `search_transcripts_endpoint()`),
      feature-flagged by `semantic_search_enabled`.

**Phase 5: Assistant integration**
- [ ] Update `services/assistant.py`'s `search` action (or add a new action) to call hybrid search;
      update the planner's system prompt guidance for when semantic recall helps.

**Phase 6: UI**
- [ ] Surface match provenance (keyword vs. semantic) in the Tape Library bank search UI in
      `static/rack.js`, similar to the existing `match_source` distinction.
- [ ] Add a "Rebuild search index" control (Queue screen or service panel) so `embed` jobs are
      visible the same way other LlmJob kinds already are.

**Phase 7: Deferred**
- [ ] Revisit sqlite-vec (or similar ANN extension) only if corpus size makes brute-force cosine a
      measured bottleneck.

## Testing considerations

- Match `tests/test_search.py`'s existing conventions (direct calls into service functions against
  a real sqlite test DB) for new chunking, embedding, and hybrid-fusion tests.
- Chunking: cover empty segments, a single very long segment, and a transcript with no segments
  (title/full_text-only).
- Cosine similarity / embedding storage: reuse or mirror whatever test pattern already covers
  `VoiceIdService._cosine_similarity()` in `tests/test_voice_id.py`.
- RRF fusion: unit-test with two known ranked lists and assert the fused order matches the expected
  RRF math, including tie-break behavior.
- `embed` LlmJob lifecycle: follow the pending -> running -> completed / cancel-mid-run /
  resurrect-on-restart coverage the other job kinds have (see `tests/test_voice_match_job.py` for
  the CPU-kind job precedent). Per this project's own lesson from PR #205 (vacuous tests / no-op
  path), any "index populated" test must first demonstrate the assertion fails without the chunk
  present, not just pass after; construct the broken state before asserting the fix.
- Since this touches a real runtime surface (bank search UI, assistant flow), a scoped
  browser-driven check of hybrid results appearing in the Tape Library search is warranted before
  merge, per this repo's testing-tier guidance, not the full e2e suite, just the affected flow.
