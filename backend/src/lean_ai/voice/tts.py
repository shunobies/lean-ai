"""Text-to-speech using kokoro-onnx.

Generates 24kHz PCM audio, encodes as WAV, returns base64-encoded chunks.
Model files (~310MB) are auto-downloaded to ~/.cache/lean_ai/kokoro/ on first use.
"""

import asyncio
import base64
import io
import logging
import os
import platform

from lean_ai.config import settings

logger = logging.getLogger(__name__)

KOKORO_SAMPLE_RATE = 24000

# ── Model file management ────────────────────────────────────────────────────

KOKORO_MODEL_FILENAME = "kokoro-v1.0.onnx"
KOKORO_VOICES_FILENAME = "voices-v1.0.bin"
KOKORO_MODEL_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.0/kokoro-v1.0.onnx"
)
KOKORO_VOICES_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.0/voices-v1.0.bin"
)

# ── Voice metadata ────────────────────────────────────────────────────────────

# Display names (voice ID first-char → human-readable language)
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

# kokoro-onnx lang codes (voice ID first-char → BCP-47)
VOICE_LANG_CODE_MAP = {
    "a": "en-us",
    "b": "en-gb",
    "e": "es",
    "f": "fr-fr",
    "h": "hi",
    "i": "it",
    "j": "ja",
    "p": "pt-br",
    "z": "zh-cn",
}


# ── Model file helpers ────────────────────────────────────────────────────────

def _kokoro_cache_dir() -> str:
    """Return the cache directory for Kokoro model files.

    Uses XDG_CACHE_HOME on Linux, ~/Library/Caches on macOS,
    falls back to ~/.cache/lean_ai/kokoro.
    """
    system = platform.system().lower()
    if system == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    else:
        base = os.environ.get(
            "XDG_CACHE_HOME", os.path.expanduser("~/.cache"),
        )
    return os.path.join(base, "lean_ai", "kokoro")


def get_model_paths() -> tuple[str, str]:
    """Return (model_path, voices_path) for Kokoro ONNX files."""
    cache_dir = _kokoro_cache_dir()
    return (
        os.path.join(cache_dir, KOKORO_MODEL_FILENAME),
        os.path.join(cache_dir, KOKORO_VOICES_FILENAME),
    )


def are_models_downloaded() -> bool:
    """Check if both model files exist on disk."""
    model_path, voices_path = get_model_paths()
    return os.path.isfile(model_path) and os.path.isfile(voices_path)


async def _download_file(url: str, dest: str) -> None:
    """Download a file from url to dest with progress logging."""
    import httpx

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".tmp"
    logger.info("TTS: downloading %s -> %s", url, dest)
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=300.0,
    ) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            downloaded = 0
            with open(tmp, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
            if total and downloaded != total:
                os.unlink(tmp)
                raise RuntimeError(
                    f"Incomplete download: {downloaded}/{total} bytes",
                )
    os.rename(tmp, dest)
    logger.info(
        "TTS: downloaded %s (%d bytes)",
        os.path.basename(dest), downloaded,
    )


async def ensure_models_downloaded() -> tuple[str, str]:
    """Download model files if not present. Returns (model_path, voices_path)."""
    model_path, voices_path = get_model_paths()
    if not os.path.isfile(model_path):
        await _download_file(KOKORO_MODEL_URL, model_path)
    if not os.path.isfile(voices_path):
        await _download_file(KOKORO_VOICES_URL, voices_path)
    return model_path, voices_path


# ── TTS Service ───────────────────────────────────────────────────────────────

class TTSService:
    """Generates speech audio from text using kokoro-onnx."""

    def __init__(self) -> None:
        self._kokoro = None  # Lazy-loaded Kokoro instance

    async def _ensure_loaded(self) -> None:
        """Lazy-load the Kokoro instance, downloading models if needed."""
        if self._kokoro is not None:
            return
        model_path, voices_path = await ensure_models_downloaded()
        from kokoro_onnx import Kokoro

        self._kokoro = Kokoro(model_path, voices_path)
        logger.info("TTS: loaded kokoro-onnx model")

    @staticmethod
    def _voice_lang_code(voice: str) -> str:
        """Map voice ID first char to kokoro-onnx BCP-47 lang code."""
        if voice:
            prefix = voice[0].lower()
            return VOICE_LANG_CODE_MAP.get(prefix, "en-us")
        return "en-us"

    @staticmethod
    def _audio_to_base64_wav(
        audio_array, sample_rate: int = KOKORO_SAMPLE_RATE,
    ) -> str:
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

        await self._ensure_loaded()
        result = await asyncio.to_thread(
            self._synthesize_sync, text, voice, speed,
        )
        return result

    def _synthesize_sync(self, text: str, voice: str, speed: float) -> dict:
        """Synchronous synthesis — runs in a thread."""
        lang = self._voice_lang_code(voice)
        samples, sample_rate = self._kokoro.create(
            text, voice=voice, speed=speed, lang=lang,
        )

        if samples is None or len(samples) == 0:
            return {"audio_base64": "", "duration_seconds": 0.0}

        duration = len(samples) / sample_rate
        audio_b64 = self._audio_to_base64_wav(samples, sample_rate)

        logger.info(
            "TTS: synthesized %.1fs audio (%d chars)", duration, len(text),
        )
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
        """Stream audio chunks using kokoro-onnx native streaming.

        Yields:
            Dicts with audio_base64 and duration_seconds per chunk.
        """
        voice = voice or settings.tts_voice
        speed = speed if speed > 0 else settings.tts_speed

        await self._ensure_loaded()
        lang = self._voice_lang_code(voice)

        async for samples, sample_rate in self._kokoro.create_stream(
            text, voice=voice, speed=speed, lang=lang,
        ):
            if samples is not None and len(samples) > 0:
                duration = len(samples) / sample_rate
                audio_b64 = self._audio_to_base64_wav(samples, sample_rate)
                yield {
                    "audio_base64": audio_b64,
                    "duration_seconds": round(duration, 2),
                }

    def list_voices(self) -> list[dict]:
        """Return available Kokoro voices with metadata."""
        voices = []
        try:
            if self._kokoro is not None:
                for vid in self._kokoro.get_voices():
                    prefix = vid[0].lower() if vid else ""
                    lang_name = VOICE_LANG_MAP.get(prefix, "Unknown")
                    gender = (
                        "female" if len(vid) > 1 and vid[1] == "f"
                        else "male"
                    )
                    voices.append({
                        "id": vid,
                        "name": vid,
                        "language": lang_name,
                        "gender": gender,
                    })
        except Exception:
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
