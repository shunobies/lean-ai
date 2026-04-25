"""Wake word detection using openWakeWord.

Runs a lightweight background listener on the microphone (16kHz 16-bit PCM).
When a wake word is detected (default: "hey_jarvis"), notifies via a callback.
"""

import asyncio
import logging
from collections.abc import Callable

from lean_ai.config import settings  # noqa: F401

logger = logging.getLogger(__name__)

# Audio parameters matching openWakeWord requirements
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_DURATION_MS = 80  # openWakeWord expects 80ms frames
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION_MS / 1000)  # 1280 samples

WAKE_WORD_MODEL = "hey_jarvis"
CONFIDENCE_THRESHOLD = 0.5


class WakeWordService:
    """Background wake word listener."""

    def __init__(self) -> None:
        self._model = None
        self._pa = None
        self._running = False
        self._listener_task: asyncio.Task | None = None
        self._on_detected: Callable | None = None
        self._on_error: Callable | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _load_model(self) -> None:
        """Load the openWakeWord model (v0.4+ API — bundled models)."""
        if self._model is not None:
            return
        from openwakeword.model import Model

        # v0.4: Model() loads all bundled models; predict() returns scores
        # for each. We filter by WAKE_WORD_MODEL in the listener loop.
        self._model = Model()
        available = list(self._model.models.keys())
        if WAKE_WORD_MODEL not in available:
            raise RuntimeError(
                f"Wake word model '{WAKE_WORD_MODEL}' not found. Available: {available}"
            )
        logger.info(
            "Wake word: loaded model '%s' (available: %s)",
            WAKE_WORD_MODEL,
            available,
        )

    def _ensure_pyaudio(self) -> None:
        """Create PyAudio instance if needed."""
        if self._pa is None:
            from lean_ai.voice.alsa_suppression import suppress_alsa_errors

            suppress_alsa_errors()
            import pyaudio

            self._pa = pyaudio.PyAudio()

    def _fire_error(self, message: str) -> None:
        """Notify via callback that the listener has failed."""
        if self._on_error and self._loop:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._on_error(message),
                    self._loop,
                )
            except Exception:
                pass

    def _listener_loop(self) -> None:
        """Blocking loop that captures audio and checks for the wake word.

        Runs in a separate thread via asyncio.to_thread.
        """
        import numpy as np
        import pyaudio

        try:
            self._load_model()
        except Exception as exc:
            logger.error("Wake word: failed to load model: %s", exc)
            self._running = False
            self._fire_error(f"Failed to load wake word model: {exc}")
            return

        try:
            self._ensure_pyaudio()
            stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
            )
        except Exception as exc:
            logger.error("Wake word: failed to open microphone: %s", exc)
            self._running = False
            self._fire_error(f"Failed to open microphone: {exc}")
            return

        logger.info("Wake word: listening for '%s'...", WAKE_WORD_MODEL)

        try:
            while self._running:
                try:
                    data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                except Exception:
                    if not self._running:
                        break
                    continue

                audio = np.frombuffer(data, dtype=np.int16)
                prediction = self._model.predict(audio)

                score = prediction.get(WAKE_WORD_MODEL, 0)
                if score > CONFIDENCE_THRESHOLD:
                    logger.info(
                        "Wake word: detected '%s' (score=%.3f)",
                        WAKE_WORD_MODEL,
                        score,
                    )
                    self._model.reset()
                    if self._on_detected and self._loop:
                        asyncio.run_coroutine_threadsafe(
                            self._on_detected(),
                            self._loop,
                        )

        except Exception as exc:
            logger.error("Wake word: listener crashed: %s", exc)
            self._fire_error(f"Wake word listener crashed: {exc}")
        finally:
            self._running = False
            stream.stop_stream()
            stream.close()

    async def start(
        self,
        on_detected: Callable,
        on_error: Callable | None = None,
    ) -> None:
        """Start listening for the wake word in a background task."""
        if self._running:
            return

        self._on_detected = on_detected
        self._on_error = on_error
        self._running = True
        self._loop = asyncio.get_event_loop()
        self._listener_task = asyncio.get_event_loop().run_in_executor(
            None,
            self._listener_loop,
        )
        logger.info("Wake word: background listener started")

    async def stop(self) -> None:
        """Stop the background listener and release the mic."""
        if not self._running:
            return

        self._running = False

        if self._listener_task is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._listener_task),
                    timeout=3.0,
                )
            except (asyncio.TimeoutError, Exception):
                pass
            self._listener_task = None

        logger.info("Wake word: listener stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    def cleanup(self) -> None:
        """Release all resources."""
        self._running = False
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None
        self._model = None
