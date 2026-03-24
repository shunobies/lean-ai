"""Text-to-speech using Kokoro.

Generates 24kHz PCM audio, encodes as WAV, returns base64-encoded chunks.
"""

import asyncio
import base64
import io
import logging
import re

from lean_ai.config import settings

logger = logging.getLogger(__name__)

KOKORO_SAMPLE_RATE = 24000

# Kokoro voice metadata: {lang_code: {voice_id: {name, gender}}}
# The full list is derived from Kokoro's built-in voices.
VOICE_LANG_MAP = {
    "a": "American English",
    "b": "British English",
    "e": "Spanish",
    "f": "French",
    "h": "Hindi",
    "i": "Italian",
    "j": "Japanese",
    "p": "Brazilian Portuguese",
    "z": "Mandarin Chinese",
}


class TTSService:
    """Generates speech audio from text."""

    def __init__(self) -> None:
        self._pipeline = None  # Lazy-loaded KPipeline
        self._current_lang = ""

    def _load_pipeline(self, lang_code: str = "a") -> None:
        """Lazy-load the Kokoro pipeline for the given language."""
        if self._pipeline is not None and self._current_lang == lang_code:
            return
        from kokoro import KPipeline

        self._pipeline = KPipeline(lang_code=lang_code)
        self._current_lang = lang_code
        logger.info("TTS: loaded Kokoro pipeline (lang=%s)", lang_code)

    @staticmethod
    def _voice_lang_code(voice: str) -> str:
        """Extract language code from voice ID (first char)."""
        if voice:
            return voice[0].lower()
        return "a"

    @staticmethod
    def _audio_to_base64_wav(audio_array, sample_rate: int = KOKORO_SAMPLE_RATE) -> str:
        """Encode a numpy audio array as a base64 WAV string."""
        import soundfile as sf

        buf = io.BytesIO()
        sf.write(buf, audio_array, sample_rate, format="WAV", subtype="PCM_16")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    async def synthesize(
        self,
        text: str,
        voice: str = "",
        speed: float = 0.0,
    ) -> dict:
        """Convert text to speech audio.

        Args:
            text: Text to speak.
            voice: Override voice (empty = use settings).
            speed: Override speed (0 = use settings).

        Returns:
            Dict with audio_base64 and duration_seconds.
        """
        voice = voice or settings.tts_voice
        speed = speed if speed > 0 else settings.tts_speed

        result = await asyncio.to_thread(
            self._synthesize_sync, text, voice, speed,
        )
        return result

    def _synthesize_sync(self, text: str, voice: str, speed: float) -> dict:
        """Synchronous synthesis — runs in a thread."""
        import numpy as np

        lang_code = self._voice_lang_code(voice)
        self._load_pipeline(lang_code)

        all_audio = []
        for _graphemes, _phonemes, audio in self._pipeline(text, voice=voice, speed=speed):
            if audio is not None:
                all_audio.append(audio)

        if not all_audio:
            return {"audio_base64": "", "duration_seconds": 0.0}

        combined = np.concatenate(all_audio)
        duration = len(combined) / KOKORO_SAMPLE_RATE
        audio_b64 = self._audio_to_base64_wav(combined)

        logger.info("TTS: synthesized %.1fs audio (%d chars)", duration, len(text))
        return {
            "audio_base64": audio_b64,
            "duration_seconds": round(duration, 2),
        }

    async def synthesize_streaming(
        self,
        text: str,
        voice: str = "",
        speed: float = 0.0,
    ):
        """Stream audio chunks for long text (sentence-by-sentence).

        Yields:
            Dicts with audio_base64 and duration_seconds per sentence.
        """
        voice = voice or settings.tts_voice
        speed = speed if speed > 0 else settings.tts_speed

        sentences = self._split_sentences(text)
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            chunk = await asyncio.to_thread(
                self._synthesize_sync, sentence, voice, speed,
            )
            if chunk["audio_base64"]:
                yield chunk

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Split text into sentences for streaming TTS."""
        return re.split(r'(?<=[.!?])\s+', text)

    def list_voices(self) -> list[dict]:
        """Return available Kokoro voices with metadata."""
        voices = []
        try:
            from kokoro import KPipeline
            for lang_code, lang_name in VOICE_LANG_MAP.items():
                try:
                    pipeline = KPipeline(lang_code=lang_code)
                    if hasattr(pipeline, "voices") and pipeline.voices:
                        for vid in pipeline.voices:
                            gender = "female" if vid.startswith(f"{lang_code}f") else "male"
                            voices.append({
                                "id": vid,
                                "name": vid,
                                "language": lang_name,
                                "gender": gender,
                            })
                except Exception:
                    pass
        except ImportError:
            pass

        if not voices:
            # Fallback: provide known default voices
            voices = [
                {"id": "af_heart", "name": "af_heart",
                 "language": "American English", "gender": "female"},
                {"id": "af_bella", "name": "af_bella",
                 "language": "American English", "gender": "female"},
                {"id": "am_adam", "name": "am_adam",
                 "language": "American English", "gender": "male"},
                {"id": "am_michael", "name": "am_michael",
                 "language": "American English", "gender": "male"},
                {"id": "bf_emma", "name": "bf_emma",
                 "language": "British English", "gender": "female"},
                {"id": "bm_george", "name": "bm_george",
                 "language": "British English", "gender": "male"},
            ]

        return voices
