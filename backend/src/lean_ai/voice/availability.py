"""Check availability of voice features with graceful degradation."""

import logging
import platform

from lean_ai.config import settings

logger = logging.getLogger(__name__)


def is_stt_available() -> bool:
    """Check if faster-whisper and PyAudio are importable and STT is enabled."""
    if not settings.enable_stt:
        return False
    try:
        import faster_whisper  # noqa: F401
        import pyaudio  # noqa: F401
        return True
    except ImportError:
        return False


def is_tts_available() -> bool:
    """Check if kokoro-onnx and soundfile are importable and TTS is enabled."""
    if not settings.enable_tts:
        return False
    try:
        import kokoro_onnx  # noqa: F401
        import soundfile  # noqa: F401
        return True
    except ImportError:
        return False


def is_wake_word_available() -> bool:
    """Check if openwakeword is importable and wake word is enabled."""
    if not settings.enable_wake_word:
        return False
    try:
        import openwakeword  # noqa: F401
        return True
    except ImportError:
        return False


def voice_status() -> dict:
    """Return feature availability dict for the health check endpoint."""
    return {
        "stt_available": is_stt_available(),
        "tts_available": is_tts_available(),
        "wake_word_available": is_wake_word_available(),
    }


def get_setup_instructions() -> dict:
    """Return per-feature install instructions."""
    system = platform.system().lower()
    if system == "linux":
        portaudio_cmd = "sudo apt install portaudio19-dev  # Debian/Ubuntu"
    elif system == "darwin":
        portaudio_cmd = "brew install portaudio"
    else:
        portaudio_cmd = "Install portaudio system library for your platform"

    return {
        "stt": {
            "pip": 'pip install "lean-ai[voice]"',
            "system": portaudio_cmd,
            "description": "Speech-to-text via faster-whisper (requires PyAudio + portaudio)",
        },
        "tts": {
            "pip": 'pip install "lean-ai[voice]"',
            "system": None,
            "description": (
                "Text-to-speech via kokoro-onnx"
                " (model files ~310MB auto-downloaded on first use)"
            ),
        },
        "wake_word": {
            "pip": 'pip install "lean-ai[voice]"',
            "system": portaudio_cmd,
            "description": "Wake word detection via openWakeWord (requires PyAudio + portaudio)",
        },
    }
