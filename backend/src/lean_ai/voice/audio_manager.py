"""Coordinates microphone access across STT and wake word services.

Only one service should hold the mic at a time. The wake word listener
pauses while STT is recording, then resumes after.
"""

import asyncio
import logging
from collections.abc import Callable

from lean_ai.voice.availability import (
    is_stt_available,
    is_tts_available,
    is_wake_word_available,
)

logger = logging.getLogger(__name__)

_audio_manager: "AudioManager | None" = None


def get_audio_manager() -> "AudioManager | None":
    """Get the singleton AudioManager (created on first call)."""
    global _audio_manager
    if _audio_manager is None:
        if is_stt_available() or is_tts_available() or is_wake_word_available():
            _audio_manager = AudioManager()
    return _audio_manager


class AudioManager:
    """Singleton that orchestrates voice services."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._wake_word_callback: Callable | None = None
        self._wake_word_was_active = False

        self.stt = None
        self.tts = None
        self.wake_word = None

        if is_stt_available():
            from lean_ai.voice.stt import STTService
            self.stt = STTService()

        if is_tts_available():
            from lean_ai.voice.tts import TTSService
            self.tts = TTSService()

        if is_wake_word_available():
            from lean_ai.voice.wake_word import WakeWordService
            self.wake_word = WakeWordService()

        logger.info(
            "AudioManager: stt=%s tts=%s wake_word=%s",
            self.stt is not None,
            self.tts is not None,
            self.wake_word is not None,
        )

    async def start_stt(self, auto_stop: bool = False) -> None:
        """Pause wake word, start STT recording."""
        async with self._lock:
            if self.stt is None:
                raise RuntimeError("STT is not available")

            # Pause wake word to release the mic
            if self.wake_word is not None and self.wake_word.is_running:
                self._wake_word_was_active = True
                await self.wake_word.stop()
            else:
                self._wake_word_was_active = False

            await self.stt.start_recording(auto_stop=auto_stop)

    async def stop_stt(self) -> dict:
        """Stop STT recording, resume wake word (if it was active)."""
        async with self._lock:
            if self.stt is None:
                return {"text": "", "language": None, "duration_seconds": 0.0}

            result = await self.stt.stop_recording()

            # Resume wake word if it was paused
            if self._wake_word_was_active and self.wake_word is not None:
                if self._wake_word_callback is not None:
                    await self.wake_word.start(self._wake_word_callback)
                self._wake_word_was_active = False

            return result

    async def start_wake_word(self, callback: Callable) -> None:
        """Start wake word listener with the given detection callback."""
        async with self._lock:
            if self.wake_word is None:
                raise RuntimeError("Wake word is not available")

            self._wake_word_callback = callback
            await self.wake_word.start(callback)

    async def stop_wake_word(self) -> None:
        """Stop wake word listener."""
        async with self._lock:
            if self.wake_word is not None:
                await self.wake_word.stop()
            self._wake_word_callback = None
            self._wake_word_was_active = False

    async def synthesize(
        self,
        text: str,
        voice: str = "",
        speed: float = 0.0,
    ) -> dict:
        """Delegate to TTS service."""
        if self.tts is None:
            raise RuntimeError("TTS is not available")
        return await self.tts.synthesize(text, voice=voice, speed=speed)

    async def synthesize_streaming(
        self,
        text: str,
        voice: str = "",
        speed: float = 0.0,
    ):
        """Delegate streaming synthesis to TTS service."""
        if self.tts is None:
            raise RuntimeError("TTS is not available")
        async for chunk in self.tts.synthesize_streaming(text, voice=voice, speed=speed):
            yield chunk

    def list_voices(self) -> list[dict]:
        """List available TTS voices."""
        if self.tts is None:
            return []
        return self.tts.list_voices()

    def cleanup(self) -> None:
        """Release all audio resources."""
        if self.stt is not None:
            self.stt.cleanup()
        if self.wake_word is not None:
            self.wake_word.cleanup()
        logger.info("AudioManager: cleaned up")
