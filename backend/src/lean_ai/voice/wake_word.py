"""Wake word detection using openWakeWord.

Runs a lightweight background listener on the microphone (16kHz 16-bit PCM).
When "Hey Computer" is detected, notifies via a callback.
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

WAKE_WORD_MODEL = "hey_computer"
CONFIDENCE_THRESHOLD = 0.5


class WakeWordService:
    """Background wake word listener."""

    def __init__(self) -> None:
        self._model = None
        self._pa = None
        self._running = False
        self._listener_task: asyncio.Task | None = None
        self._on_detected: Callable | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _load_model(self) -> None:
        """Load the openWakeWord model."""
        if self._model is not None:
            return
        import openwakeword
        from openwakeword.model import Model

        openwakeword.utils.download_models()
        self._model = Model(wakeword_models=[WAKE_WORD_MODEL])
        logger.info("Wake word: loaded model '%s'", WAKE_WORD_MODEL)

    def _ensure_pyaudio(self) -> None:
        """Create PyAudio instance if needed."""
        if self._pa is None:
            import pyaudio
            self._pa = pyaudio.PyAudio()

    def _listener_loop(self) -> None:
        """Blocking loop that captures audio and checks for the wake word.

        Runs in a separate thread via asyncio.to_thread.
        """
        import numpy as np
        import pyaudio

        self._load_model()
        self._ensure_pyaudio()

        stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE,
        )

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

                for model_name, score in prediction.items():
                    if score > CONFIDENCE_THRESHOLD:
                        logger.info(
                            "Wake word: detected '%s' (score=%.3f)",
                            model_name, score,
                        )
                        self._model.reset()
                        if self._on_detected and self._loop:
                            self._loop.call_soon_threadsafe(self._on_detected)
                        break
        finally:
            stream.stop_stream()
            stream.close()

    async def start(self, on_detected: Callable) -> None:
        """Start listening for the wake word in a background task."""
        if self._running:
            return

        self._on_detected = on_detected
        self._running = True
        self._loop = asyncio.get_event_loop()
        self._listener_task = asyncio.get_event_loop().run_in_executor(
            None, self._listener_loop,
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
                    asyncio.shield(self._listener_task), timeout=3.0,
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
