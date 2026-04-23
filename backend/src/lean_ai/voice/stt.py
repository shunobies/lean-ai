"""Speech-to-text using faster-whisper with PyAudio mic capture.

Disabled when voice extras are not installed or LEAN_AI_ENABLE_STT is False.
"""

import asyncio
import logging
import struct
import time
from collections.abc import Callable

from lean_ai.config import settings

logger = logging.getLogger(__name__)

# Audio capture parameters
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SIZE = 1024
FORMAT_WIDTH = 2  # 16-bit = 2 bytes

# RMS threshold for silence detection (empirical default)
SILENCE_RMS_THRESHOLD = 500


class STTService:
    """Manages microphone capture and transcription."""

    def __init__(self) -> None:
        self._model = None  # Lazy-loaded WhisperModel
        self._pa = None  # PyAudio instance
        self._stream = None  # Active PyAudio stream
        self._recording = False
        self._audio_buffer: list[bytes] = []
        self._lock = asyncio.Lock()
        self._auto_stop = False
        self._on_auto_stop: Callable | None = None
        self._auto_stop_loop: asyncio.AbstractEventLoop | None = None
        self._record_task: asyncio.Task | None = None
        self._record_start_time: float = 0.0

    def _load_model(self) -> None:
        """Lazy-load the faster-whisper model based on settings."""
        if self._model is not None:
            return
        from faster_whisper import WhisperModel

        # Always CPU — GPU is reserved for the LLM (Ollama)
        self._model = WhisperModel(
            settings.stt_model,
            device="cpu",
            compute_type="int8",
            cpu_threads=settings.stt_cpu_threads,
        )
        logger.info(
            "STT: loaded model %s on cpu (int8, threads=%d)",
            settings.stt_model, settings.stt_cpu_threads,
        )

    async def warm_up(self) -> None:
        """Pre-load the Whisper model in a background thread."""
        if self._model is not None:
            return
        await asyncio.to_thread(self._load_model)

    def _ensure_pyaudio(self) -> None:
        """Create PyAudio instance if needed."""
        if self._pa is None:
            from lean_ai.voice.alsa_suppression import suppress_alsa_errors
            suppress_alsa_errors()
            import pyaudio
            self._pa = pyaudio.PyAudio()

    @staticmethod
    def _rms(chunk: bytes) -> float:
        """Compute RMS amplitude of a 16-bit PCM audio chunk."""
        count = len(chunk) // FORMAT_WIDTH
        if count == 0:
            return 0.0
        samples = struct.unpack(f"<{count}h", chunk)
        sum_sq = sum(s * s for s in samples)
        return (sum_sq / count) ** 0.5

    def _recording_loop(self) -> None:
        """Blocking loop that captures audio from the mic.

        Runs in a separate thread via asyncio.to_thread.
        """
        import pyaudio

        self._ensure_pyaudio()
        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE,
        )

        silence_frames = 0
        silence_limit = int(
            settings.stt_silence_threshold * SAMPLE_RATE / CHUNK_SIZE
        )

        logger.info("STT: recording started (auto_stop=%s)", self._auto_stop)

        try:
            while self._recording:
                try:
                    data = self._stream.read(CHUNK_SIZE, exception_on_overflow=False)
                except Exception:
                    break
                self._audio_buffer.append(data)

                if self._auto_stop:
                    rms = self._rms(data)
                    if rms < SILENCE_RMS_THRESHOLD:
                        silence_frames += 1
                        has_audio = len(self._audio_buffer) > silence_limit
                        if silence_frames >= silence_limit and has_audio:
                            logger.info(
                                "STT: auto-stop after %.1fs silence",
                                settings.stt_silence_threshold,
                            )
                            self._recording = False
                            if self._on_auto_stop and self._auto_stop_loop:
                                asyncio.run_coroutine_threadsafe(
                                    self._on_auto_stop(),
                                    self._auto_stop_loop,
                                )
                    else:
                        silence_frames = 0
        finally:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
                self._stream = None

    async def start_recording(
        self, auto_stop: bool = False, on_auto_stop: Callable | None = None,
    ) -> None:
        """Begin capturing audio from the microphone."""
        async with self._lock:
            if self._recording:
                return

            self._recording = True
            self._auto_stop = auto_stop
            self._on_auto_stop = on_auto_stop
            self._auto_stop_loop = asyncio.get_event_loop() if on_auto_stop else None
            self._audio_buffer = []
            self._record_start_time = time.monotonic()

            self._record_task = asyncio.get_event_loop().run_in_executor(
                None, self._recording_loop,
            )

    async def stop_recording(self) -> dict:
        """Stop capturing and transcribe the buffered audio.

        Returns:
            Dict with text, language, and duration_seconds.
        """
        async with self._lock:
            self._recording = False

            if self._record_task is not None:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self._record_task), timeout=3.0,
                    )
                except (asyncio.TimeoutError, Exception):
                    pass
                self._record_task = None

            if not self._audio_buffer:
                return {"text": "", "language": None, "duration_seconds": 0.0}

            duration = time.monotonic() - self._record_start_time
            audio_data = b"".join(self._audio_buffer)
            self._audio_buffer = []

            # Try an LLM-backed audio handler first when a role is flagged.
            # Falls back to faster-whisper on CapabilityError or any runtime
            # failure so the user's flow never breaks.
            text, language = await self._transcribe_via_llm_or_whisper(audio_data)

            return {
                "text": text,
                "language": language,
                "duration_seconds": round(duration, 2),
            }

    async def _transcribe_via_llm_or_whisper(
        self, audio_data: bytes,
    ) -> tuple[str, str | None]:
        """Dispatch: flagged LLM role → Whisper fallback.

        The first branch wraps the raw PCM in a WAV container (PyAudio
        captures ``paInt16`` mono 16kHz) and calls ``chat_raw`` on the
        flagged client with a transcribe-only prompt.  On failure
        (``CapabilityError`` from the provider, a network hiccup, etc.)
        logs a warning and drops through to the existing Whisper path.
        """
        try:
            from lean_ai.routers.dependencies import resolve_audio_handler
        except ImportError:
            # Circular-import guard — only possible during test module
            # isolation.  Defer to Whisper.
            resolve_audio_handler = None  # type: ignore[assignment]

        handler = resolve_audio_handler() if resolve_audio_handler else None
        if handler is not None:
            try:
                text, language = await self._transcribe_via_llm(
                    audio_data, handler,
                )
                return text, language
            except Exception as exc:  # catches CapabilityError + transport errors
                logger.warning(
                    "LLM audio handler %s refused/failed: %s; falling back to Whisper",
                    handler.provider_name, exc,
                )

        return await asyncio.to_thread(self._transcribe, audio_data)

    async def _transcribe_via_llm(
        self, pcm_data: bytes, client,
    ) -> tuple[str, str | None]:
        """Transcribe PCM bytes via a flagged LLM client.

        Wraps ``pcm_data`` in a 16kHz mono WAV container, base64-encodes
        it, attaches via ``media_messages.attach_audio`` in the client's
        native shape, and sends a transcribe-only prompt.
        """
        import base64
        import io
        import wave

        from lean_ai.llm.media_messages import attach_audio

        # Wrap raw PCM in a WAV container so the LLM sees a proper header.
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav_file:
            wav_file.setnchannels(CHANNELS)
            wav_file.setsampwidth(FORMAT_WIDTH)
            wav_file.setframerate(SAMPLE_RATE)
            wav_file.writeframes(pcm_data)
        wav_bytes = buf.getvalue()
        audio_b64 = base64.b64encode(wav_bytes).decode("ascii")

        system_prompt = (
            "Transcribe the following audio verbatim. "
            "Respond with ONLY the transcription — no preamble, no "
            "apologies, no explanatory text."
        )
        user_messages = attach_audio(
            [{"role": "user", "content": ""}],
            audio_b64,
            "audio/wav",
            provider=client.provider_name,
        )
        messages = [
            {"role": "system", "content": system_prompt},
            *user_messages,
        ]

        text, _metrics = await client.chat_raw(messages, temperature=0.0)
        text = (text or "").strip()
        logger.info(
            "STT via LLM (%s): %d bytes PCM → %d chars",
            client.provider_name, len(pcm_data), len(text),
        )
        # LLM transcription doesn't expose a detected language — return None.
        return text, None

    def _transcribe(self, audio_data: bytes) -> tuple[str, str | None]:
        """Transcribe raw PCM audio bytes. Runs in a thread."""
        import numpy as np

        self._load_model()

        audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0

        language = settings.stt_language or None
        segments, info = self._model.transcribe(
            audio_array,
            beam_size=settings.stt_beam_size,
            language=language,
        )

        text_parts = [seg.text for seg in segments]
        full_text = " ".join(text_parts).strip()

        detected_lang = info.language if hasattr(info, "language") else None
        logger.info(
            "STT: transcribed %d bytes → %d chars (lang=%s)",
            len(audio_data), len(full_text), detected_lang,
        )

        return full_text, detected_lang

    @property
    def is_recording(self) -> bool:
        return self._recording

    def cleanup(self) -> None:
        """Release microphone and model resources."""
        self._recording = False
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None
        self._model = None
