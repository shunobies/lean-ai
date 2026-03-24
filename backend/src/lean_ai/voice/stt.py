"""Speech-to-text using faster-whisper with PyAudio mic capture.

Disabled when voice extras are not installed or LEAN_AI_ENABLE_STT is False.
"""

import asyncio
import logging
import struct
import time

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
                    else:
                        silence_frames = 0
        finally:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
                self._stream = None

    async def start_recording(self, auto_stop: bool = False) -> None:
        """Begin capturing audio from the microphone."""
        async with self._lock:
            if self._recording:
                return

            self._recording = True
            self._auto_stop = auto_stop
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

            text, language = await asyncio.to_thread(
                self._transcribe, audio_data,
            )

            return {
                "text": text,
                "language": language,
                "duration_seconds": round(duration, 2),
            }

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
