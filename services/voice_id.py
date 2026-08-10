"""Voice identification service — enroll and identify speakers from voice samples.

Uses speaker embedding extraction (speechbrain or similar) to build a voice
identification database. When pyannote/speechbrain are not installed, falls
back to a fingerprint-based approach using audio feature extraction.
"""
import os
import json
import datetime
import hashlib
import threading
import numpy as np
from pathlib import Path
from typing import Optional

from database import VoiceProfile, VoiceClip, utcnow_naive

# app.py's BASE_DIR (Path(__file__).parent.resolve()) is the project root;
# this file lives one level down in services/, so parent.parent gets back
# to the same root without importing app.py (which would be circular).
_DEFAULT_VOICES_DIR = str(Path(__file__).resolve().parent.parent / "data" / "voices")

# Every backend can degrade to MFCC at runtime (see _extract_embedding), so this
# id is reachable no matter which package is installed.
_MFCC_MODEL_ID = "MFCC fingerprint (librosa)"

# One registry for the model id each backend stamps onto the embeddings it
# produces. Read by backend_name, _extract_embedding, and
# compatible_embedding_models() so the three can't drift apart when a backend is
# added. The "none" entry is a display string only, never a stored model id.
_BACKEND_MODEL_IDS = {
    "speechbrain": "speechbrain/spkrec-ecapa-voxceleb",
    "pyannote": "pyannote/wespeaker-voxceleb-resnet34-LM",
    "librosa_mfcc": _MFCC_MODEL_ID,
    "none": "No backend available — install speechbrain",
}

# Backends whose embeddings live in a different vector space than MFCC's, so
# falling back to MFCC under them produces a clip that cannot be compared
# against anything they enrolled.
_STRONG_BACKENDS = ("speechbrain", "pyannote")


class VoiceIdentificationService:
    """Enroll known speakers and identify speakers in audio."""

    def __init__(self, voices_dir: str = _DEFAULT_VOICES_DIR):
        self.voices_dir = voices_dir
        os.makedirs(voices_dir, exist_ok=True)
        self._backend = self._detect_backend()
        # This service is a module-level singleton (voice_id_service) reached
        # from two different threads: the event loop, via the /api/voices
        # routes, and a ThreadPoolExecutor worker, via the voice_match job's
        # run_in_executor call in services/llm_jobs.py. Every piece of mutable
        # state below therefore needs an explicit thread story.
        self._model_lock = threading.Lock()  # guards the two lazy model caches
        self._classifier = None  # cached speechbrain EncoderClassifier
        self._pyannote_inference = None  # cached pyannote Inference wrapper
        # Per-thread storage for _last_backend_error (see the property below).
        # No initial value is set here: the getter defaults to None, and
        # seeding it from the constructing thread would not be visible to any
        # other thread anyway.
        self._error_state = threading.local()

    @property
    def _last_backend_error(self) -> Optional[str]:
        """Why the last embedding extraction on *this thread* failed.

        A diagnostic channel, not shared state: enroll()/add_clip() read it
        immediately after their own _extract_embedding() call to append a
        reason to the ValueError they raise. It is stored per-thread and
        cleared at the top of every _extract_embedding() call, so a failing
        voice_match job on an executor thread can never be the reason an
        enrollment request on the event loop reports, and a failure from one
        call can never be reported as the reason for a later one.
        """
        return getattr(self._error_state, "message", None)

    @_last_backend_error.setter
    def _last_backend_error(self, message: Optional[str]) -> None:
        self._error_state.message = message

    def _detect_backend(self) -> str:
        try:
            import speechbrain  # noqa
            return "speechbrain"
        except ImportError:
            pass
        try:
            import warnings
            # pyannote.audio warns on import if torchcodec isn't installed —
            # harmless, we hand it preloaded waveforms and never touch the
            # torchcodec decoding path (same suppression as
            # services/diarization.py _check_pyannote).
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=UserWarning)
                import pyannote.audio  # noqa
            return "pyannote"
        except ImportError:
            pass
        try:
            import librosa  # noqa
            return "librosa_mfcc"
        except ImportError:
            pass
        return "none"

    @property
    def backend_name(self) -> str:
        return _BACKEND_MODEL_IDS.get(self._backend, "unknown")

    def compatible_embedding_models(self) -> set[str]:
        """Model ids whose stored embeddings identify() can still compare against.

        Wider than {backend_name} on purpose: _extract_embedding falls back to
        MFCC when the primary backend is installed but throws at runtime, so a
        profile tagged with the MFCC id is matchable under any live backend.
        Callers use this for pre-flight guards (services/llm_jobs.py's
        voice_match branch), so it must never be narrower than what identify()
        actually accepts, or a job that would have matched gets refused.
        """
        if self._backend == "none":
            return set()
        primary = _BACKEND_MODEL_IDS.get(self._backend)
        return {m for m in (primary, _MFCC_MODEL_ID) if m}

    def _is_degraded_model(self, model_id: Optional[str]) -> bool:
        return model_id == _MFCC_MODEL_ID and self._backend in _STRONG_BACKENDS

    def degraded_model_warning(self, model_id: Optional[str]) -> Optional[str]:
        if not self._is_degraded_model(model_id):
            return None
        return (
            f"This audio could not be processed by the {self.backend_name} backend, so a "
            f"lower-accuracy MFCC fingerprint was stored instead. It can only be matched "
            f"against other MFCC clips, so voice match may not find this speaker."
        )

    def _ensure_not_orphan_model(self, db, user_id: int, model_id: str) -> None:
        if not self._is_degraded_model(model_id):
            return
        other = (
            db.query(VoiceProfile)
            .filter(
                VoiceProfile.user_id == user_id,
                VoiceProfile.embedding_model.isnot(None),
                VoiceProfile.embedding_model != model_id,
                VoiceProfile.sample_count > 0,
            )
            .first()
        )
        if other is None:
            return
        reason = f" ({self._last_backend_error})" if self._last_backend_error else ""
        raise ValueError(
            f"This audio could not be processed by the {self.backend_name} backend, so it fell "
            f"back to an MFCC fingerprint. Profile '{other.name}' is enrolled under "
            f"{other.embedding_model}, and the two can never be compared, so this clip would "
            f"be stored as a speaker voice match can never find. Re-record or repair the audio, "
            f"or check the backend's dependencies.{reason}"
        )

    def enroll(
        self,
        db,
        user_id: int,
        name: str,
        audio_path: str,
        notes: str = "",
        hf_token: Optional[str] = None,
    ) -> VoiceProfile:
        result = self._extract_embedding(audio_path, hf_token=hf_token)
        if result is None:
            if self._backend == "none":
                raise ValueError(
                    "No voice embedding backend available. "
                    "Install speechbrain (pip install speechbrain), pyannote.audio "
                    "(pip install pyannote.audio torch), or librosa "
                    "(pip install librosa) to enable voice enrollment."
                )
            reason = f" ({self._last_backend_error})" if self._last_backend_error else ""
            raise ValueError(
                f"Voice embedding extraction failed using the {self.backend_name} "
                f"backend. Check that the audio file is valid and the backend's "
                f"dependencies (e.g. torch, torchaudio) are working correctly.{reason}"
            )
        embedding, model_id = result
        self._ensure_not_orphan_model(db, user_id, model_id)

        profile = db.query(VoiceProfile).filter(
            VoiceProfile.user_id == user_id, VoiceProfile.name == name
        ).first()
        if not profile:
            profile = VoiceProfile(
                user_id=user_id, name=name, embedding=None,
                embedding_model=model_id, sample_count=0, notes=notes,
            )
            db.add(profile)
            db.commit()
        elif notes:
            profile.notes = notes
            db.commit()

        self._ensure_clip_compatible(db, profile, embedding, model_id)
        self._persist_clip(db, profile, audio_path, embedding, model_id)
        db.refresh(profile)
        return profile

    def add_clip(
        self,
        db,
        profile_id: int,
        user_id: int,
        audio_path: str,
        source_transcript_id: Optional[int] = None,
        hf_token: Optional[str] = None,
    ) -> VoiceClip:
        profile = db.query(VoiceProfile).filter(
            VoiceProfile.id == profile_id, VoiceProfile.user_id == user_id
        ).first()
        if not profile:
            raise ValueError(f"Voice profile {profile_id} not found")

        result = self._extract_embedding(audio_path, hf_token=hf_token)
        if result is None:
            reason = f" ({self._last_backend_error})" if self._last_backend_error else ""
            raise ValueError(
                f"Voice embedding extraction failed using the {self.backend_name} "
                f"backend.{reason}"
            )
        embedding, model_id = result
        self._ensure_not_orphan_model(db, user_id, model_id)

        self._ensure_clip_compatible(db, profile, embedding, model_id)

        return self._persist_clip(db, profile, audio_path, embedding, model_id, source_transcript_id)

    def _ensure_clip_compatible(self, db, profile: VoiceProfile, embedding, model_id: str) -> None:
        """Shared by enroll() and add_clip(): refuse clips that can't be
        averaged with the profile's existing ones — a different embedding
        model, or (for legacy NULL-model rows) a different vector length."""
        existing_clips = db.query(VoiceClip).filter(VoiceClip.voice_profile_id == profile.id).all()
        mismatch = next((c for c in existing_clips if c.embedding_model and c.embedding_model != model_id), None)
        if mismatch:
            raise ValueError(
                f"This clip was extracted using {model_id}, but profile '{profile.name}' "
                f"already has clips extracted using {mismatch.embedding_model}. Mixing "
                f"embedding models within one profile isn't supported — switch backends "
                f"back, or enroll this speaker as a separate profile."
            )
        emb_len = len(embedding)
        dim_mismatch = next((c for c in existing_clips if c.embedding and len(c.embedding) != emb_len), None)
        if dim_mismatch:
            raise ValueError(
                f"This clip's embedding has {emb_len} dimensions, but profile '{profile.name}' "
                f"already has a clip with {len(dim_mismatch.embedding)} dimensions — likely "
                f"extracted by a different backend before embedding models were tracked. "
                f"Remove the profile's older clips or enroll this speaker as a separate profile."
            )

    def _persist_clip(
        self,
        db,
        profile: VoiceProfile,
        audio_path: str,
        embedding,
        model_id: str,
        source_transcript_id: Optional[int] = None,
    ) -> VoiceClip:
        clip = VoiceClip(
            voice_profile_id=profile.id,
            audio_path=audio_path,
            embedding=embedding.tolist() if isinstance(embedding, np.ndarray) else embedding,
            embedding_model=model_id,
            source_transcript_id=source_transcript_id,
        )
        db.add(clip)
        db.commit()
        self._recompute_profile_embedding(db, profile)
        return clip

    def remove_clip(self, db, profile_id: int, user_id: int, clip_id: int) -> bool:
        profile = db.query(VoiceProfile).filter(
            VoiceProfile.id == profile_id, VoiceProfile.user_id == user_id
        ).first()
        if not profile:
            return False
        clip = db.query(VoiceClip).filter(
            VoiceClip.id == clip_id, VoiceClip.voice_profile_id == profile.id
        ).first()
        if not clip:
            return False
        try:
            os.remove(clip.audio_path)
        except OSError:
            pass
        db.delete(clip)
        db.commit()
        self._recompute_profile_embedding(db, profile)
        return True

    def _recompute_profile_embedding(self, db, profile: VoiceProfile) -> None:
        clips = db.query(VoiceClip).filter(VoiceClip.voice_profile_id == profile.id).all()
        if not clips:
            profile.embedding = None
            profile.sample_count = 0
        else:
            stacked = np.array([c.embedding for c in clips])
            profile.embedding = np.mean(stacked, axis=0).tolist()
            profile.sample_count = len(clips)
            latest_model = next((c.embedding_model for c in reversed(clips) if c.embedding_model), None)
            if latest_model:
                profile.embedding_model = latest_model
        profile.updated_at = utcnow_naive()
        db.commit()

    def identify(self, db, user_id: int, audio_path: str, threshold: float = 0.65, hf_token: Optional[str] = None) -> list[dict]:
        return self.identify_detailed(
            db, user_id, audio_path, threshold=threshold, hf_token=hf_token
        )["matches"]

    def identify_detailed(
        self,
        db,
        user_id: int,
        audio_path: str,
        threshold: float = 0.65,
        hf_token: Optional[str] = None,
    ) -> dict:
        outcome = {
            "matches": [],
            "probe_model": None,
            "degraded": False,
            "compared": 0,
            "skipped_model_mismatch": 0,
            "warning": None,
        }
        result = self._extract_embedding(audio_path, hf_token=hf_token)
        if result is None:
            reason = f" ({self._last_backend_error})" if self._last_backend_error else ""
            outcome["warning"] = (
                f"Voice embedding extraction failed using the {self.backend_name} "
                f"backend, so this audio could not be matched.{reason}"
            )
            return outcome
        probe_embedding, probe_model = result
        outcome["probe_model"] = probe_model
        outcome["degraded"] = self._is_degraded_model(probe_model)

        profiles = db.query(VoiceProfile).filter(VoiceProfile.user_id == user_id).all()

        results = []
        for profile in profiles:
            if profile.embedding is None:
                continue
            # A NULL embedding_model is a pre-migration row, treated as
            # compatible with any probe. services/llm_jobs.py's voice_match
            # branch pre-flights this same condition (via
            # compatible_embedding_models()) so it can refuse a job before
            # extracting a clip per segment. Keep the two in step.
            if profile.embedding_model and profile.embedding_model != probe_model:
                outcome["skipped_model_mismatch"] += 1
                continue
            stored = np.array(profile.embedding)
            if len(stored) != len(probe_embedding):
                continue
            outcome["compared"] += 1
            similarity = self._cosine_similarity(probe_embedding, stored)
            if similarity >= threshold:
                results.append({
                    "id": profile.id,
                    "name": profile.name,
                    "similarity": round(float(similarity), 4),
                    "sample_count": profile.sample_count,
                })

        results.sort(key=lambda x: x["similarity"], reverse=True)
        outcome["matches"] = results
        if outcome["degraded"]:
            outcome["warning"] = self.degraded_model_warning(probe_model)
        return outcome

    def list_profiles(self, db, user_id: int) -> list[dict]:
        profiles = (
            db.query(VoiceProfile)
            .filter(VoiceProfile.user_id == user_id)
            .order_by(VoiceProfile.name)
            .all()
        )
        return [
            {
                "id": p.id,
                "name": p.name,
                "sample_count": p.sample_count,
                "embedding_model": p.embedding_model,
                "notes": p.notes,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "clips": [
                    {
                        "id": c.id,
                        "created_at": c.created_at.isoformat() if c.created_at else None,
                        "source_transcript_id": c.source_transcript_id,
                    }
                    for c in db.query(VoiceClip)
                    .filter(VoiceClip.voice_profile_id == p.id)
                    .order_by(VoiceClip.created_at)
                    .all()
                ],
            }
            for p in profiles
        ]

    def delete_profile(self, db, user_id: int, profile_id: int) -> bool:
        p = db.query(VoiceProfile).filter(
            VoiceProfile.id == profile_id, VoiceProfile.user_id == user_id
        ).first()
        if not p:
            return False
        clips = db.query(VoiceClip).filter(VoiceClip.voice_profile_id == p.id).all()
        for clip in clips:
            try:
                os.remove(clip.audio_path)
            except OSError:
                pass
            db.delete(clip)
        db.delete(p)
        db.commit()
        return True

    def _extract_embedding(self, audio_path: str, hf_token: Optional[str] = None) -> Optional[tuple]:
        # Start each extraction with a clean error slot. Without this, the
        # "don't clobber the primary backend's error" guard in _embed_mfcc
        # would also refuse to overwrite an error left behind by an earlier
        # call on this thread, so enroll()/add_clip() would report a stale
        # reason for a fresh failure.
        self._last_backend_error = None
        if self._backend == "speechbrain":
            embedding = self._embed_speechbrain(audio_path)
            if embedding is not None:
                return embedding, _BACKEND_MODEL_IDS["speechbrain"]
            return self._mfcc_fallback(audio_path)
        elif self._backend == "pyannote":
            embedding = self._embed_pyannote(audio_path, hf_token=hf_token)
            if embedding is not None:
                return embedding, _BACKEND_MODEL_IDS["pyannote"]
            return self._mfcc_fallback(audio_path)
        elif self._backend == "librosa_mfcc":
            return self._mfcc_fallback(audio_path)
        return None

    def _mfcc_fallback(self, audio_path: str) -> Optional[tuple]:
        embedding = self._embed_mfcc(audio_path)
        return (embedding, _MFCC_MODEL_ID) if embedding is not None else None

    def _get_classifier(self):
        """Build the SpeechBrain classifier once and cache it — loading it
        from disk on every enroll/identify call is slow.

        Double-checked under _model_lock: two threads (an /api/voices route on
        the event loop and the voice_match job's executor thread) can reach
        here at the same moment, and an unguarded check-then-act would let both
        run from_hparams() against the same savedir, wasting the load at best
        and racing on the same on-disk model directory at worst."""
        if self._classifier is None:
            with self._model_lock:
                if self._classifier is None:
                    from speechbrain.inference.speaker import EncoderClassifier
                    self._classifier = EncoderClassifier.from_hparams(
                        source="speechbrain/spkrec-ecapa-voxceleb",
                        savedir=os.path.join(self.voices_dir, "_models", "ecapa"),
                        run_opts={"device": "cpu"},
                    )
        return self._classifier

    def _embed_speechbrain(self, audio_path: str) -> Optional[np.ndarray]:
        """Extract embedding using SpeechBrain's ECAPA-TDNN."""
        try:
            import torchaudio

            classifier = self._get_classifier()
            signal, fs = torchaudio.load(audio_path)
            # Resample to 16kHz if needed
            if fs != 16000:
                resampler = torchaudio.transforms.Resample(fs, 16000)
                signal = resampler(signal)
            # Use first 30 seconds if longer
            if signal.shape[1] > 16000 * 30:
                signal = signal[:, :16000 * 30]
            embedding = classifier.encode_batch(signal).squeeze().numpy()
            return embedding
        except Exception as e:
            self._last_backend_error = f"speechbrain: {e}"
            return None

    def _get_pyannote_inference(self, hf_token: Optional[str] = None):
        """Same lazy cache, same double-checked lock, as _get_classifier."""
        if self._pyannote_inference is None:
            with self._model_lock:
                if self._pyannote_inference is None:
                    # Same gated-model auth as diarization: raise the clear
                    # MissingTokenError before importing pyannote, instead
                    # of a cryptic 401 (issue #119). _embed_pyannote's
                    # catch-all records the message as the backend error.
                    from services.diarization import resolve_hf_token
                    token = resolve_hf_token(hf_token)
                    from pyannote.audio import Model, Inference
                    model = Model.from_pretrained(
                        "pyannote/wespeaker-voxceleb-resnet34-LM",
                        token=token,
                    )
                    self._pyannote_inference = Inference(model, window="whole")
        return self._pyannote_inference

    def _embed_pyannote(self, audio_path: str, hf_token: Optional[str] = None) -> Optional[np.ndarray]:
        try:
            import torch
            import soundfile as sf

            inference = self._get_pyannote_inference(hf_token)
            data, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
            waveform = torch.from_numpy(data.T)
            if waveform.shape[0] > 1:
                # embedding models expect mono — downmix rather than erroring
                # out and silently falling back to MFCC
                waveform = waveform.mean(0).reshape(1, -1)
            if waveform.shape[1] > sample_rate * 30:
                waveform = waveform[:, :sample_rate * 30]
            embedding = inference({"waveform": waveform, "sample_rate": sample_rate})
            return np.asarray(embedding).reshape(-1)
        except Exception as e:
            self._last_backend_error = f"pyannote: {e}"
            return None

    def _embed_mfcc(self, audio_path: str) -> Optional[np.ndarray]:
        """Fallback embedding using MFCC features (less accurate but no ML deps)."""
        try:
            import librosa
            y, sr = librosa.load(audio_path, sr=16000, duration=30)
            mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20)
            # Aggregate over time
            embedding = np.mean(mfcc, axis=1)
            return embedding
        except Exception as e:
            if not self._last_backend_error:
                self._last_backend_error = f"librosa_mfcc: {e}"
            return None

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))


voice_id_service = VoiceIdentificationService()