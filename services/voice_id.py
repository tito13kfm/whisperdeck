"""Voice identification service — enroll and identify speakers from voice samples.

Uses speaker embedding extraction (speechbrain or similar) to build a voice
identification database. When pyannote/speechbrain are not installed, falls
back to a fingerprint-based approach using audio feature extraction.
"""
import os
import json
import datetime
import hashlib
import numpy as np
from typing import Optional

from database import VoiceProfile


class VoiceIdentificationService:
    """Enroll known speakers and identify speakers in audio."""

    def __init__(self, db_session, voices_dir: str = "data/voices"):
        self.db = db_session
        self.voices_dir = voices_dir
        os.makedirs(voices_dir, exist_ok=True)
        self._backend = self._detect_backend()

    def _detect_backend(self) -> str:
        """Detect which embedding backend is available."""
        try:
            import speechbrain  # noqa
            return "speechbrain"
        except ImportError:
            pass
        try:
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
        names = {
            "speechbrain": "speechbrain/spkrec-ecapa-voxceleb",
            "pyannote": "pyannote/embedding",
            "librosa_mfcc": "MFCC fingerprint (librosa)",
            "none": "No backend available — install speechbrain",
        }
        return names.get(self._backend, "unknown")

    def enroll(
        self,
        name: str,
        audio_path: str,
        notes: str = "",
    ) -> VoiceProfile:
        """Enroll a speaker by name from an audio sample."""
        embedding = self._extract_embedding(audio_path)
        if embedding is None:
            raise ValueError(
                f"No voice embedding backend available. "
                f"Install speechbrain: pip install speechbrain"
            )

        existing = self.db.query(VoiceProfile).filter(VoiceProfile.name == name).first()
        if existing:
            existing.embedding = embedding.tolist() if isinstance(embedding, np.ndarray) else embedding
            existing.sample_count += 1
            existing.notes = notes or existing.notes
            existing.updated_at = datetime.datetime.utcnow()
            profile = existing
        else:
            profile = VoiceProfile(
                name=name,
                embedding=embedding.tolist() if isinstance(embedding, np.ndarray) else embedding,
                embedding_model=self.backend_name,
                sample_count=1,
                notes=notes,
            )
            self.db.add(profile)

        self.db.commit()
        return profile

    def identify(self, audio_path: str, threshold: float = 0.65) -> list[dict]:
        """Identify a speaker from an audio sample. Returns ranked candidates."""
        probe_embedding = self._extract_embedding(audio_path)
        if probe_embedding is None:
            return []

        profiles = self.db.query(VoiceProfile).all()
        if not profiles:
            return []

        results = []
        for profile in profiles:
            stored = np.array(profile.embedding)
            similarity = self._cosine_similarity(probe_embedding, stored)
            if similarity >= threshold:
                results.append({
                    "id": profile.id,
                    "name": profile.name,
                    "similarity": round(float(similarity), 4),
                    "sample_count": profile.sample_count,
                })

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results

    def list_profiles(self) -> list[dict]:
        return [
            {
                "id": p.id,
                "name": p.name,
                "sample_count": p.sample_count,
                "embedding_model": p.embedding_model,
                "notes": p.notes,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in self.db.query(VoiceProfile).order_by(VoiceProfile.name).all()
        ]

    def delete_profile(self, profile_id: int) -> bool:
        p = self.db.query(VoiceProfile).filter(VoiceProfile.id == profile_id).first()
        if not p:
            return False
        self.db.delete(p)
        self.db.commit()
        return True

    def _extract_embedding(self, audio_path: str) -> Optional[np.ndarray]:
        """Extract a speaker embedding vector from an audio file."""
        if self._backend == "speechbrain":
            return self._embed_speechbrain(audio_path)
        elif self._backend == "librosa_mfcc":
            return self._embed_mfcc(audio_path)
        return None

    def _embed_speechbrain(self, audio_path: str) -> Optional[np.ndarray]:
        """Extract embedding using SpeechBrain's ECAPA-TDNN."""
        try:
            import torch
            import torchaudio
            from speechbrain.inference.speaker import EncoderClassifier

            classifier = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir=os.path.join(self.voices_dir, "_models", "ecapa"),
                run_opts={"device": "cpu"},
            )
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
        except Exception:
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
        except Exception:
            return None

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))