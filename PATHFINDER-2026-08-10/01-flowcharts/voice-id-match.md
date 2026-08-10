# Feature: voice-id-match

## Sources consulted
- `services/voice_id.py` full file (1-551)
- `services/llm_jobs.py` lines 1-55, 760-938 (voice_match job branch)
- `services/relabel.py` full file (1-110)
- `database/__init__.py` 126-138 (RelabelHistory), 243-271 (VoiceProfile, VoiceClip)
- `app.py` 3438-3574 (/api/voices/* routes), 2823-2850 (/voice-match route), 2455-2494 (/relabel-undo), 321,399-402 (job serialization)
- `static/rack.js` 4300-4370 (voiceMatchSummaryUnit, similarityPct)

## Concrete findings
**Backend cascade** (voice_id.py:83-106, _detect_backend): speechbrain (85-86) -> pyannote (89-99) -> librosa (101-104) -> "none" (106). Runtime per-call fallback is separate: `_extract_embedding` (430-449) tries detected primary backend, on any exception in `_embed_speechbrain`(475-493) or `_embed_pyannote`(508-526) calls `_mfcc_fallback` -> `_embed_mfcc` (451-453,528-540). A degraded MFCC clip against an existing strong-backend profile is refused by `_ensure_not_orphan_model` (139-161).

**Enroll** (163-207): _extract_embedding -> _ensure_not_orphan_model guard -> get-or-create VoiceProfile (190-202, DB write) -> _ensure_clip_compatible guard (238-259) -> _persist_clip (261-280): creates VoiceClip row (DB write) then _recompute_profile_embedding (302-315), means all clip embeddings, writes VoiceProfile.embedding/sample_count (DB write).

**Manual identify** (identify_detailed, 322-381, hit from POST /api/voices/identify app.py:3489-3520): for each enrolled profile, skip on embedding_model mismatch (361-363) or dim mismatch (365-366), else `_cosine_similarity` (368,542-548). If similarity >= threshold, append {id,name,similarity,sample_count} (369-375); sorted descending (377). This full per-profile similarity list returns in JSON (app.py:3510-3520), rendered in manual-identify UI — **this path is NOT discarded**, genuine one-off consumer of per-match scores.

**Voice-match batch job** — the path PR #311 touched (POST /transcripts/{id}/voice-match app.py:2823-2849 -> enqueue_llm_job(kind="voice_match") -> llm_jobs.py:769-937):
- Pre-flight: backend none (770-772), no audio (773-775), no enrolled clips (776-783), roster entirely incompatible (794-801).
- Per segment (823-875): extract clip (extract_clips_concat, 836-839), run identify_detailed off-thread (846-851), read outcome["matches"] (863).
- **If matches non-empty (868-873)**: `changed.append((i, seg.get("speaker") or ""))` — **only segment index and old speaker string, no score** (869). `new_segments[i] = {**seg, "speaker": matches[0]["name"]}` — **only the winning profile's name written; matches[0]["similarity"] read on the very next line but never attached to the segment** (870-873). `match_sims.setdefault(matches[0]["name"], []).append(float(matches[0]["similarity"]))` diverts similarity into a **per-profile-name list**, not per-segment (817-821,871-873).
- After loop: `record_relabel(db, transcript, "voice_match", changed, ...)` (886-888) writes RelabelHistory.inverse = {"segments":[{"index":i,"speaker":old}],...} — **no similarity field exists in this schema or call**.
- `transcript.segments = new_segments` (890) is the persisted, relabel-consuming data — carries new speaker name and nothing about confidence.
- `job.result_json["speakers"]` (903-921) aggregates match_sims into min/mean/max similarity **per matched profile name (not per segment)**, returned via serialize_llm_job to frontend and rendered as chips in voiceMatchSummaryUnit (rack.js:4338-4370) — the actual PR #311 deliverable, a coarse job-scoped per-profile summary.

**Undo confirms the discard**: POST /relabel-undo (app.py:2455-2494) reads entry.inverse.get("segments",[]) and only has index/speaker to work with — no similarity value anywhere in the record to read even if it wanted to.

### Verdict on the discarded-score claim: **CONFIRMED, precisely**
The per-segment cosine similarity (voice_id.py:368) is used twice on its way out of identify_detailed — as a threshold gate (369) and sort key picking matches[0] (377) — so it does influence which speaker gets written. But once llm_jobs.py:870 builds the new segment, only matches[0]["name"] survives; the float is read once more (872) purely to feed a profile-name-keyed aggregate (match_sims -> job.result_json["speakers"]). That aggregate reaches the UI once as a summary chip, but:
- Never attached to the individual segment (transcript.segments[i] has no similarity/confidence key).
- Never captured by record_relabel (changed tuples carry only index + old speaker), so RelabelHistory.inverse has nothing to show.

The per-segment score is discarded before it reaches the transcript's segment data and before it reaches the relabel-history/undo record — it survives only as a transient, coarser, per-profile min/mean/max summary tied to that one job's result_json, overwritten the next time voice-match reruns on that transcript.

## Mermaid flowchart

```mermaid
flowchart TD
  A1["POST /api/voices/enroll<br/>app.py:3444"]
  A2["POST /api/voices/identify<br/>app.py:3489"]
  A3["POST /api/transcripts/id/voice-match<br/>app.py:2823"]

  A1 --> ENROLL["enroll<br/>voice_id.py:163"]
  A2 --> IDDET1["identify_detailed (manual probe)<br/>voice_id.py:322"]
  A3 --> ENQ["enqueue_llm_job kind=voice_match<br/>app.py:2848"]
  ENQ --> JOBLOOP["voice_match job branch<br/>llm_jobs.py:769"]

  subgraph BACKEND["Backend auto-detect, constructor time"]
    DET["_detect_backend<br/>voice_id.py:83"]
    DET -->|"speechbrain installed"| SB["backend = speechbrain<br/>voice_id.py:85-86"]
    DET -->|"else pyannote installed"| PY["backend = pyannote<br/>voice_id.py:89-99"]
    DET -->|"else librosa installed"| LB["backend = librosa_mfcc<br/>voice_id.py:101-104"]
    DET -->|"else"| NB["backend = none<br/>voice_id.py:106"]
  end

  ENROLL --> EXTRACT1["_extract_embedding<br/>voice_id.py:430"]
  IDDET1 --> EXTRACT2["_extract_embedding, probe clip<br/>voice_id.py:338"]

  EXTRACT1 --> DISPATCH{"self._backend"}
  EXTRACT2 --> DISPATCH

  DISPATCH -->|"speechbrain"| SBEMB["_embed_speechbrain<br/>voice_id.py:475"]
  DISPATCH -->|"pyannote"| PYEMB["_embed_pyannote<br/>voice_id.py:508"]
  DISPATCH -->|"librosa_mfcc"| MFCC1["_mfcc_fallback then _embed_mfcc<br/>voice_id.py:451, 528"]
  DISPATCH -->|"none"| NONERES["return None<br/>voice_id.py:449"]

  SBEMB -.->|"uses cached model"| CLS["_get_classifier lazy singleton<br/>voice_id.py:455"]
  SBEMB -->|"exception"| MFCCFB1["fallback: _mfcc_fallback<br/>voice_id.py:441"]
  SBEMB -->|"success"| EMBOK1["embedding + speechbrain model id<br/>voice_id.py:440"]

  PYEMB -.->|"uses cached model"| PYI["_get_pyannote_inference lazy singleton<br/>voice_id.py:495"]
  PYEMB -->|"exception"| MFCCFB2["fallback: _mfcc_fallback<br/>voice_id.py:446"]
  PYEMB -->|"success"| EMBOK2["embedding + pyannote model id<br/>voice_id.py:445"]

  MFCC1 --> EMBOK3["embedding + MFCC fingerprint model id<br/>voice_id.py:453"]
  MFCCFB1 --> EMBOK3
  MFCCFB2 --> EMBOK3

  NONERES --> ERR1["enroll/add_clip: raise ValueError, no backend<br/>voice_id.py:174-186"]
  NONERES --> ERR2["identify_detailed: warning, empty matches<br/>voice_id.py:339-345"]
  NONERES --> ERR3["voice_match job: fail, no backend<br/>llm_jobs.py:770-772"]

  EMBOK1 --> ORPHAN["_ensure_not_orphan_model guard<br/>voice_id.py:139"]
  EMBOK2 --> ORPHAN
  EMBOK3 --> ORPHAN
  ORPHAN -->|"other strong-backend profile exists, this clip is degraded MFCC"| ERR4["raise ValueError, unmatchable clip<br/>voice_id.py:154-161"]
  ORPHAN -->|"ok"| GETPROFILE["get-or-create VoiceProfile<br/>voice_id.py:190-202"]
  GETPROFILE --> DBPROFILE[("DB write: VoiceProfile row<br/>database/__init__.py:243")]
  DBPROFILE --> COMPAT["_ensure_clip_compatible guard<br/>voice_id.py:238"]
  COMPAT -->|"model or dim mismatch"| ERR5["raise ValueError, mixed models/dims<br/>voice_id.py:245-259"]
  COMPAT -->|"ok"| PERSIST["_persist_clip<br/>voice_id.py:261"]
  PERSIST --> DBCLIP[("DB write: VoiceClip row<br/>database/__init__.py:258")]
  PERSIST --> RECOMP["_recompute_profile_embedding<br/>voice_id.py:302"]
  RECOMP --> DBPROFILE2[("DB write: VoiceProfile.embedding = mean of clips<br/>voice_id.py:308-315")]

  EMBOK1 --> COMPARE
  EMBOK2 --> COMPARE
  EMBOK3 --> COMPARE
  COMPARE["loop enrolled VoiceProfiles<br/>voice_id.py:350-376"]
  COMPARE -->|"embedding_model mismatch"| SKIPMM["skipped_model_mismatch++<br/>voice_id.py:361-363"]
  COMPARE -->|"dim mismatch"| SKIPDIM["skip, uncounted<br/>voice_id.py:365-366"]
  COMPARE --> COS["_cosine_similarity per profile<br/>voice_id.py:368, 542-548"]
  COS -->|">= threshold"| MATCHLIST["append id, name, similarity, sample_count<br/>voice_id.py:369-375"]
  COS -->|"below threshold"| DROP1["dropped, not in matches<br/>voice_id.py:369"]
  MATCHLIST --> SORT["sort matches desc by similarity<br/>voice_id.py:377"]
  SORT --> OUTCOME1["outcome.matches returned<br/>voice_id.py:378-381"]
  OUTCOME1 --> RESP1["JSON response, matches incl. similarity<br/>app.py:3510-3520"]
  RESP1 --> UIIDENT["Manual identify UI shows real per-match score, not discarded<br/>rack.js ~4322"]

  JOBLOOP --> PREFLIGHT{"pre-flight checks<br/>llm_jobs.py:770-801"}
  PREFLIGHT -->|"no backend"| ERR3
  PREFLIGHT -->|"no audio file"| ERR6["fail: no stored audio<br/>llm_jobs.py:773-775"]
  PREFLIGHT -->|"no enrolled clips"| ERR7["fail: no enrolled voices<br/>llm_jobs.py:776-783"]
  PREFLIGHT -->|"roster all incompatible"| ERR8["fail: backend cannot match roster<br/>llm_jobs.py:794-801"]
  PREFLIGHT -->|"ok"| SEGLOOP["for each segment<br/>llm_jobs.py:823-875"]
  SEGLOOP --> CANCELCHK{"job cancelled?<br/>llm_jobs.py:831-833"}
  CANCELCHK -->|"yes"| STOP1["return, transcript left untouched<br/>llm_jobs.py:833"]
  CANCELCHK -->|"no"| CLIPX["extract_clips_concat, per-segment clip<br/>llm_jobs.py:836-839"]
  CLIPX --> IDDET2CALL["identify_detailed on executor thread<br/>llm_jobs.py:846-851"]
  IDDET2CALL --> EXTRACT2
  IDDET2CALL --> OUTCOME2["outcome: matches sorted desc, degraded, skipped_model_mismatch<br/>llm_jobs.py:862-867"]
  OUTCOME2 -->|"matches empty"| NOMATCH["segment left unchanged<br/>llm_jobs.py:868 false branch"]
  OUTCOME2 -->|"matches non-empty"| PICKTOP["matches[0] = highest-similarity profile<br/>voice_id.py:377 / llm_jobs.py:868-873"]

  PICKTOP --> SCOREUSE1["read matches0.similarity<br/>llm_jobs.py:872"]
  PICKTOP --> NAMEONLY["new_segments[i] = seg with speaker = matches0.name ONLY<br/>llm_jobs.py:870"]
  SCOREUSE1 --> MATCHSIMS["match_sims[name].append(similarity), per-profile list, NOT per-segment<br/>llm_jobs.py:817-821, 871-873"]

  NAMEONLY --> CHANGEDLIST["changed.append(index, old_speaker), no score carried<br/>llm_jobs.py:869"]
  NAMEONLY --> SEGWRITE[("DB write: transcript.segments = new_segments<br/>speaker name only, no similarity field<br/>llm_jobs.py:890")]

  CHANGEDLIST --> RECORDRELABEL["record_relabel kind=voice_match<br/>relabel.py:46"]
  RECORDRELABEL --> DBRELABEL[("DB write: RelabelHistory.inverse = index + old_speaker only<br/>no similarity field in schema<br/>database/__init__.py:135, relabel.py:64-67")]

  DBRELABEL --> UNDO["POST /relabel-undo applies inverse patch<br/>app.py:2455, 2474-2477"]
  UNDO --> UNDOAPPLY["segments[i].speaker = old speaker; reads index/speaker only<br/>app.py:2477"]

  MATCHSIMS --> JOBRESULT["job.result_json.speakers: name, count, min/mean/max similarity<br/>PER-PROFILE aggregate, not per-segment<br/>llm_jobs.py:903-922"]
  JOBRESULT --> SERIALIZE["serialize_llm_job include_result=True<br/>app.py:402"]
  SERIALIZE --> SUMMARYUI["voiceMatchSummaryUnit renders chips with aggregate min/mean/max<br/>rack.js:4338-4370"]

  SEGWRITE -.->|"score never reaches persisted segment"| DEADEND["No per-line similarity anywhere in transcript.segments or RelabelHistory"]
  UNDOAPPLY -.->|"nothing to read"| DEADEND
  DEADEND --> VERDICT["Verdict: per-segment score discarded before relabel data / undo; survives only as a transient per-profile job summary"]
```

## External dependencies
Backend libraries: speechbrain EncoderClassifier, torchaudio, pyannote.audio Model/Inference, torch, soundfile, librosa MFCC, numpy. DB: VoiceProfile, VoiceClip, RelabelHistory (SQLAlchemy).

## Confidence and gaps
High confidence on discard verdict — traced by exact line for both "used" path (threshold gate, sort, job-summary aggregate, UI chip) and "discarded" path (segment write, RelabelHistory.inverse schema, undo apply). Not independently verified: exact rendering trigger for voiceMatchSummaryUnit in the DOM polling code; whether any test asserts the per-segment-discard behavior explicitly (test file exists, not opened). services/diarization.py and extract_clips_concat not read beyond name/call-site confirmation, out of this feature's scope.
