"""Text-to-speech using kokoro-onnx.

Generates 24kHz PCM audio, encodes as WAV, returns base64-encoded chunks.
Model files are auto-downloaded to ~/.cache/lean_ai/kokoro/ on first use.
Default fp16 model is ~169MB; fp32 is ~311MB; int8 is ~88MB.
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

KOKORO_VOICES_FILENAME = "voices-v1.0.bin"
KOKORO_VOICES_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.0/voices-v1.0.bin"
)

_RELEASE_BASE = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.0/"
)
MODEL_VARIANTS: dict[str, tuple[str, str]] = {
    "fp32": ("kokoro-v1.0.onnx", _RELEASE_BASE + "kokoro-v1.0.onnx"),
    "fp16": ("kokoro-v1.0.fp16.onnx", _RELEASE_BASE + "kokoro-v1.0.fp16.onnx"),
    "int8": ("kokoro-v1.0.int8.onnx", _RELEASE_BASE + "kokoro-v1.0.int8.onnx"),
}

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


def _model_variant() -> tuple[str, str]:
    """Return (filename, url) for the configured model quality."""
    quality = settings.tts_model_quality.lower()
    if quality not in MODEL_VARIANTS:
        logger.warning(
            "TTS: unknown quality %r, falling back to fp16", quality,
        )
        quality = "fp16"
    return MODEL_VARIANTS[quality]


def get_model_paths() -> tuple[str, str]:
    """Return (model_path, voices_path) for Kokoro ONNX files."""
    cache_dir = _kokoro_cache_dir()
    filename, _ = _model_variant()
    return (
        os.path.join(cache_dir, filename),
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
    ) as client, client.stream("GET", url) as response:
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
    _, model_url = _model_variant()
    if not os.path.isfile(model_path):
        await _download_file(model_url, model_path)
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
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        # Optimized ONNX session — graph optimization + threading
        sess_opts = ort.SessionOptions()
        sess_opts.graph_optimization_level = (
            ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        )
        auto_threads = min(os.cpu_count() or 4, 8)
        sess_opts.intra_op_num_threads = (
            settings.tts_cpu_threads if settings.tts_cpu_threads > 0
            else auto_threads
        )
        sess_opts.inter_op_num_threads = 1
        session = ort.InferenceSession(
            model_path,
            sess_options=sess_opts,
            providers=["CPUExecutionProvider"],
        )
        self._kokoro = Kokoro.from_session(session, voices_path)
        quality = settings.tts_model_quality.lower()
        logger.info("TTS: loaded kokoro-onnx %s model (optimized)", quality)

        # Warmup — first inference triggers ONNX JIT optimizations
        await asyncio.to_thread(
            self._kokoro.create,
            "warmup", voice="af_heart", speed=1.0, lang="en-us",
        )
        logger.info("TTS: warmup complete")

    @staticmethod
    def _voice_lang_code(voice: str) -> str:
        """Map voice ID first char to kokoro-onnx BCP-47 lang code."""
        if voice:
            prefix = voice[0].lower()
            return VOICE_LANG_CODE_MAP.get(prefix, "en-us")
        return "en-us"

    @staticmethod
    def _split_for_tts(text: str, max_chars: int = 300) -> list[str]:
        """Split text into chunks safe for kokoro-onnx phoneme limits.

        kokoro-onnx crashes when phonemes exceed 510. Split on sentence
        boundaries first, then clause boundaries, then word boundaries.
        300 chars is conservative (~200-300 phonemes, well under 510).
        """
        import re

        text = text.strip()
        if not text:
            return []
        if len(text) <= max_chars:
            return [text]

        chunks: list[str] = []

        # Split on sentence boundaries
        sentences = re.split(r"(?<=[.!?])\s+", text)

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) <= max_chars:
                chunks.append(sentence)
            else:
                # Split long sentences on clause boundaries
                clauses = re.split(r"(?<=[,;:\u2014\u2013])\s+", sentence)
                for clause in clauses:
                    clause = clause.strip()
                    if not clause:
                        continue
                    if len(clause) <= max_chars:
                        chunks.append(clause)
                    else:
                        # Last resort: split on word boundaries
                        words = clause.split()
                        current = ""
                        for word in words:
                            candidate = f"{current} {word}".strip()
                            if len(candidate) <= max_chars:
                                current = candidate
                            else:
                                if current:
                                    chunks.append(current)
                                current = word
                        if current:
                            chunks.append(current)

        return chunks if chunks else [text[:max_chars]]

    @staticmethod
    def _audio_to_base64_wav(
        audio_array, sample_rate: int = KOKORO_SAMPLE_RATE,
    ) -> str:
        """Encode a numpy audio array as a base64 WAV string."""
        import soundfile as sf

        buf = io.BytesIO()
        sf.write(buf, audio_array, sample_rate, format="WAV", subtype="PCM_16")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    @staticmethod
    def _audio_to_base64_pcm(audio_array) -> str:
        """Encode a numpy float32 audio array as base64 Int16 PCM (no WAV header).

        Lighter than WAV: no 44-byte header overhead per chunk, and clients
        can construct AudioBuffers synchronously without decodeAudioData().
        """
        import numpy as np

        clipped = np.clip(audio_array, -1.0, 1.0)
        int16_data = (clipped * 32767).astype(np.int16)
        return base64.b64encode(int16_data.tobytes()).decode("ascii")

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
        import numpy as np

        lang = self._voice_lang_code(voice)
        all_samples = []
        rate = KOKORO_SAMPLE_RATE

        for chunk_text in self._split_for_tts(text):
            try:
                samples, sample_rate = self._kokoro.create(
                    chunk_text, voice=voice, speed=speed, lang=lang,
                )
                if samples is not None and len(samples) > 0:
                    all_samples.append(samples)
                    rate = sample_rate
            except IndexError:
                logger.warning(
                    "TTS: kokoro IndexError on chunk (%d chars), skipping",
                    len(chunk_text),
                )

        if not all_samples:
            return {"audio_base64": "", "duration_seconds": 0.0}

        combined = np.concatenate(all_samples)
        duration = len(combined) / rate
        audio_b64 = self._audio_to_base64_wav(combined, rate)

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

        Splits text into safe chunks to avoid kokoro-onnx's 510-phoneme
        limit crash (off-by-one in create_stream).

        Yields:
            Dicts with audio_base64 and duration_seconds per chunk.
        """
        voice = voice or settings.tts_voice
        speed = speed if speed > 0 else settings.tts_speed

        await self._ensure_loaded()
        lang = self._voice_lang_code(voice)

        for chunk_text in self._split_for_tts(text):
            try:
                async for samples, sample_rate in self._kokoro.create_stream(
                    chunk_text, voice=voice, speed=speed, lang=lang,
                ):
                    if samples is not None and len(samples) > 0:
                        duration = len(samples) / sample_rate
                        audio_b64 = self._audio_to_base64_wav(
                            samples, sample_rate,
                        )
                        yield {
                            "audio_base64": audio_b64,
                            "duration_seconds": round(duration, 2),
                        }
            except IndexError:
                logger.warning(
                    "TTS: kokoro IndexError on chunk (%d chars), skipping",
                    len(chunk_text),
                )

    async def synthesize_streaming_pcm(
        self,
        text: str,
        voice: str = "",
        speed: float = 0.0,
    ):
        """Stream raw PCM audio chunks for zero-decode playback.

        Same as synthesize_streaming but yields raw Int16 PCM (no WAV header)
        for lower overhead and synchronous client-side AudioBuffer construction.

        Yields:
            Dicts with pcm_base64, sample_rate, and duration_seconds per chunk.
        """
        voice = voice or settings.tts_voice
        speed = speed if speed > 0 else settings.tts_speed

        await self._ensure_loaded()
        lang = self._voice_lang_code(voice)
        chunks = self._split_for_tts(text)

        # Pipeline: produce audio chunks via asyncio.Queue so phonemization
        # of the next text chunk overlaps with delivery of the current one.
        output_queue: asyncio.Queue = asyncio.Queue()

        async def _produce():
            for chunk_text in chunks:
                try:
                    async for samples, sample_rate in self._kokoro.create_stream(
                        chunk_text, voice=voice, speed=speed, lang=lang,
                    ):
                        if samples is not None and len(samples) > 0:
                            await output_queue.put((samples, sample_rate))
                except IndexError:
                    logger.warning(
                        "TTS: kokoro IndexError on chunk (%d chars), skipping",
                        len(chunk_text),
                    )
            await output_queue.put(None)  # sentinel

        producer = asyncio.create_task(_produce())

        try:
            while True:
                item = await output_queue.get()
                if item is None:
                    break
                samples, sample_rate = item
                duration = len(samples) / sample_rate
                audio_b64 = self._audio_to_base64_pcm(samples)
                yield {
                    "pcm_base64": audio_b64,
                    "sample_rate": sample_rate,
                    "duration_seconds": round(duration, 2),
                }
        finally:
            await producer  # ensure clean exit

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
