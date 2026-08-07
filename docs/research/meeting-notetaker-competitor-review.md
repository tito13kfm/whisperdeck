# Meeting-notetaker / notes-app competitor review

Date: 2026-08-06
Sources:
- https://github.com/Zackriya-Solutions/meetily (meeting-minutes assistant)
- https://github.com/reorproject/reor (archived AI note-taking app)
- https://github.com/fastrepl/anarlog (open-source continuation of Hyprnote)

A fourth repo, https://github.com/francescopace/espectre, was requested but is a Wi-Fi
CSI-based motion-detection firmware project for Home Assistant/ESP32 — no overlap with
transcription, notes, or meetings. No findings recorded for it; likely a mixed-up URL
(corrected to anarlog above, per follow-up).

Each review below assumes WhisperDeck's current state: multi-provider transcription
(Moonshine/faster-whisper local, Groq/OpenAI/Replicate/OpenRouter/custom cloud), heuristic +
pyannote.audio diarization, voice enrollment/identification (speechbrain/pyannote/librosa
MFCC), hotword glossary feeding an LLM correction pass, LLM correction + summarization, run
history/version diffing, a background job queue with cancel/resume/retry, multi-user accounts
with admin roles, and FTS5 keyword search (shipped via #108; semantic search still open, #218).

---

## meetily (Zackriya-Solutions/meetily)

Local-first meeting assistant. Now a native desktop app (Tauri + Rust backend using
whisper-rs/Parakeet, Next.js frontend), SQLite storage. It **used to** ship a Python/FastAPI +
whisper.cpp backend architecturally close to WhisperDeck's own stack, but that backend is now
archived in-repo ("no longer supports the standalone FastAPI backend as the active application
API") — they moved off a server model to an embedded-desktop model, the opposite direction from
WhisperDeck's self-hosted-server design goal. Free "Community Edition" + paid PRO tier
(templates, export, calendar — some "coming soon").

**Relevant findings:**

- **Live capture with dual-source audio (mic + system audio, mixed) during an active meeting.**
  This is real, shipped capability, and it's the one concrete precedent for WhisperDeck's parked
  "live conversational capture" gap (#263): capturing *both sides* of a call, not just the mic.
  They credit **Screenpipe** as prior art for the hard, OS-specific part (cross-platform
  system-audio taps) — worth studying if WhisperDeck ever pursues capturing remote-party audio,
  not just the local mic. A `BLUETOOTH_PLAYBACK_NOTICE.md` in their repo root flags known edge-case
  bugs with Bluetooth output devices during system-audio capture — a caution flag, not a solution.
- **GPU backend auto-detection with fallback priority** (CUDA → Metal → Vulkan → OpenBLAS → CPU)
  at startup, feature-flagged in their build. Clean "just works" UX pattern. Worth a quick check
  of whether WhisperDeck's faster-whisper path already auto-detects CUDA vs. falling back
  silently/manually — cheap polish item if not, no dedicated issue found for it.
- **Diarization is still "coming soon" / PRO-only** in meetily — WhisperDeck is already ahead here
  (heuristic + pyannote.audio both shipped). No full-text or semantic search found in their docs
  either — same gap WhisperDeck has, no prior art to borrow from this repo on that front.
- No hotword/glossary system, no correction pass, no job queue with cancel/resume/retry, no run
  history/version diffing, no multi-user/admin story (single-user desktop app) — WhisperDeck is
  meaningfully more mature on all of these; nothing to reverse-engineer here.

---

## reor (reorproject/reor)

Archived (read-only as of 2026-03-07) Electron AI note-taking app, local-first, notes as plain
markdown files. Confirmed by reading source directly (`electron/main/vector-database/*`,
`src/lib/db.ts`, `src/components/Sidebars/*`), not just the README, since its docs domain
(reorproject.org) is currently unreachable.

Core loop: fixed-size chunking (500 chars, markdown stripped) → local ONNX embeddings
(transformers.js "Xenova" ports, model swappable by language/hardware) → LanceDB (embedded
file-based vector store, one table per embedding-model+directory) → nearest-neighbor retrieval
feeds both a "related notes" sidebar and a RAG chat.

**Relevant findings, mapped to WhisperDeck's open search work (#218, #242):**

- **Cross-corpus semantic search** (embed query → nearest-neighbor across all notes) is a
  different capability from literal/keyword search — retrieval by meaning, not exact match. This
  is exactly #218's scope (hybrid semantic + FTS5). Confirms the approach is real and shippable
  even in a single-maintainer project.
- **Automatic "related items" sidebar**: while viewing one note, a live nearest-neighbor query
  surfaces other semantically-similar notes with zero manual tagging/linking. Maps directly onto
  a transcript library — "other meetings about this topic" — as a cheap byproduct once embeddings
  exist for #218. Also relevant to #241 (meeting knowledge layer / entity extraction): this is a
  lighter-weight *complement* to full entity extraction for surfacing related meetings, not a
  replacement — entity extraction gives structured people/projects/decisions, embedding similarity
  gives free-form "this feels related."
- **Chat-with-your-corpus**: an LLM answers questions against the whole vault, not just one note.
  Directly relevant to #242 (grounded Q&A over transcripts) — a second working precedent alongside
  the GrayBox reference already cited in that issue.
- **Cross-encoder reranking pass** on top of initial nearest-neighbor hits (`Xenova/bge-reranker-base`,
  filtering to score > 0) — a cheap second pass that meaningfully improves top-k relevance without
  touching the index. Worth considering for #218/#242's retrieval quality once the base pipeline
  ships.
- **User-facing vector/keyword weight slider** (default 0.7 vector weight) in their search UI — a
  reusable control-surface idea (let the user bias toward literal vs. semantic) even though their
  underlying hybrid implementation has a real bug (next point).
- **Caution, not a pattern to copy**: their "hybrid" search is not two independent indexes merged.
  It re-runs vector search first, then keyword-matches *only within those already-retrieved
  vector candidates*. A document that doesn't rank in the vector top-N can never surface via an
  exact keyword hit, no matter how precise the match. **If #218's hybrid fusion is implemented,
  query the FTS5 index and the vector index independently and merge results — don't filter one by
  the other's output.** This is the single most actionable technical warning from this review.
- **Storage note**: reor uses LanceDB (a standalone embedded vector DB). Given WhisperDeck's
  SQLite-only storage constraint, `sqlite-vec` (a SQLite extension) is the closer analog, not
  LanceDB itself. The "drop and recreate the table when the embedding model changes" migration
  pattern they use is portable regardless of which vector store #218 lands on.
- **Naive fixed-size chunking (500 chars, no sentence-boundary logic) shipped fine** — evidence
  that #218 doesn't need sophisticated chunking to deliver a useful MVP.
- Not relevant: markdown editor/wiki-links, filesystem-as-source-of-truth (a regression versus
  WhisperDeck's SQLite + run-history model), Electron shell, and their commented-out/dead
  "AI writing assistant" ghost-text feature — an abandoned experiment, not evidence for anything.

---

## anarlog (fastrepl/anarlog, open-source continuation of Hyprnote)

Native desktop app (Tauri + Rust, React/TS, Cargo/pnpm/Turbo monorepo). Bot-free, privacy-first
meeting notetaker: captures mic + system audio locally with no bot joining the call, transcribes
on-device or via a configured provider, generates AI summaries, stores locally in SQLite with
optional encrypted cloud sync. Confirmed via README + docs pages, not assumed from the name.

**Relevant findings:**

- **Live streaming transcription during capture**, distinct from "after recording" models — a
  floating control bar shows live captions while recording. This is a second, independent
  precedent (after meetily) that live-STT display is achievable without a full duplex
  conversational/TTS loop — relevant to scoping #263 down to "show live text" as an achievable
  first slice, separate from the harder "spoken response" half of that issue.
- **Ambient, auto-triggered capture**: calendar-linked "start when meeting begins," OS
  accessibility-permission mic-activity detection to prompt recording of unscheduled calls, and
  auto-stop when the meeting app releases the mic. Different capture philosophy (always-on
  desktop monitor vs. deliberate record/upload) — the mechanism itself assumes native desktop
  OS-level permissions WhisperDeck (browser + server) doesn't have, so not directly portable, but
  useful context for how competitors think about the front door WhisperDeck's already-shipped
  "studio framing" (#264, closed) addressed differently.
- **Three-way note model: Memo / Summary / Transcript** kept as separate first-class artifacts —
  the user's own manual notes, the AI summary, and the raw transcript are distinct, all
  viewable/exportable together. No open WhisperDeck issue maps to this directly (studio framing's
  issues are all closed/shipped); recording here as a structuring idea worth a look if a future
  session revisits how correction/summary/transcript are presented together, but not actioned
  against any issue tonight.
- **Diarization is API-based** (`crates/api-pyannote`, hosted call) rather than embedded, and
  speaker correction is "select label, apply to all instances in this transcript" only — no
  evidence of cross-session voice enrollment tied to named contacts. **WhisperDeck's local
  pyannote.audio + voice-ID enrollment (speechbrain/pyannote/librosa) is already ahead of this
  competitor** — confirms an existing strength, no action needed.
- **Explicit "offline readiness checklist"**: docs walk users through confirming *both*
  transcription and "intelligence" (LLM) are pointed at local providers before calling the setup
  actually offline, plus a recommendation to test-record before relying on it. Given WhisperDeck
  already mixes local (Moonshine/faster-whisper) and cloud providers per-request, a visible
  "which stages are local right now" status could be a small settings-page UX add — no dedicated
  issue found; noting here rather than filing new work.
- **Non-retroactive prompt/template changes**: editing a summary template never rewrites past
  summaries, only affects future/regenerated ones. Reasonable, boring, well-specified rule — worth
  a quick sanity check that WhisperDeck's correction/summary settings changes don't silently
  affect already-generated output, though this wasn't verified against WhisperDeck's own code in
  this pass (external-research-only session).
- Full-text/cross-item search: inconclusive from public docs (no search-related `.mdx` page
  found, GitHub code search needed auth). Treat as absence-of-evidence, not evidence of absence.
- Not relevant: Tauri/Rust packaging, calendar integrations, contacts view, CloudSync
  (E2E-encrypted sync solves "local app wants some cloud," the inverse of WhisperDeck's
  already-server-hosted situation) — none of this transfers architecturally.

---

## Cross-cutting recommendations → existing issues

| Finding | Issue updated |
|---|---|
| sqlite-vec as SQLite-native analog to LanceDB; cross-encoder reranking; naive chunking is enough for MVP; **hybrid search must query both indexes independently, never filter one by the other's output** | [#218](https://github.com/tito13kfm/whisperdeck/issues/218) Semantic RAG search |
| Chat-with-corpus (reor) is a second working precedent for grounded Q&A, alongside the existing GrayBox reference | [#242](https://github.com/tito13kfm/whisperdeck/issues/242) Ask your meetings |
| Dual-source (mic + system audio) capture (meetily) + Screenpipe as reference for cross-platform system-audio taps; live-caption-only slice (anarlog) as an achievable first cut separate from TTS response | [#263](https://github.com/tito13kfm/whisperdeck/issues/263) Live conversational capture |
| Embedding-similarity "related meetings" as a cheap complement to full entity extraction; anarlog's local voice-ID confirmed as an existing WhisperDeck strength vs. a real competitor | [#241](https://github.com/tito13kfm/whisperdeck/issues/241) Meeting knowledge layer |

No new issues filed. Two smaller ideas (GPU auto-detect-and-fallback at startup, an "offline
readiness" status indicator, non-retroactive-settings sanity check) didn't map to any existing
issue and are recorded above rather than filed as new work — worth a look next planning pass, not
urgent enough to interrupt anything in flight.
