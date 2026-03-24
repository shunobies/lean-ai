"""Voice endpoints — STT, TTS, wake word control."""

import asyncio
import json
import logging
import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from lean_ai.routers.models import (
    EnsureModelsResponse,
    STTStartRequest,
    STTStopResponse,
    TTSRequest,
    TTSResponse,
    VoiceConfigRequest,
    VoiceInfo,
    VoiceListResponse,
    VoiceStatusResponse,
)

logger = logging.getLogger(__name__)

voice_router = APIRouter()


def _unavailable(feature: str) -> JSONResponse:
    """Return 501 with setup instructions for a missing voice feature."""
    try:
        from lean_ai.voice.availability import get_setup_instructions
        instructions = get_setup_instructions()
        info = instructions.get(feature, {})
    except ImportError:
        info = {"pip": 'pip install "lean-ai[voice]"'}

    return JSONResponse(
        status_code=501,
        content={
            "detail": f"Voice feature '{feature}' is not available. "
            f"Install with: {info.get('pip', 'pip install lean-ai[voice]')}",
            "setup": info,
        },
    )


def _get_manager():
    """Get the AudioManager or None."""
    try:
        from lean_ai.voice.audio_manager import get_audio_manager
        return get_audio_manager()
    except ImportError:
        return None


# ── STT endpoints ──


@voice_router.post("/voice/stt/start")
async def stt_start(request: STTStartRequest):
    """Start microphone recording for STT."""
    mgr = _get_manager()
    if mgr is None or mgr.stt is None:
        return _unavailable("stt")

    try:
        await mgr.start_stt(auto_stop=request.auto_stop)
        return {"status": "recording"}
    except Exception as e:
        logger.exception("STT start failed")
        return JSONResponse(status_code=500, content={"detail": str(e)})


@voice_router.post("/voice/stt/stop", response_model=STTStopResponse)
async def stt_stop():
    """Stop recording and return transcribed text."""
    mgr = _get_manager()
    if mgr is None or mgr.stt is None:
        return _unavailable("stt")

    try:
        result = await mgr.stop_stt()
        return STTStopResponse(**result)
    except Exception as e:
        logger.exception("STT stop failed")
        return JSONResponse(status_code=500, content={"detail": str(e)})


# ── TTS endpoints ──


@voice_router.post("/voice/tts", response_model=TTSResponse)
async def tts_synthesize(request: TTSRequest):
    """Convert text to speech audio (base64 WAV)."""
    mgr = _get_manager()
    if mgr is None or mgr.tts is None:
        return _unavailable("tts")

    try:
        result = await mgr.synthesize(
            request.text, voice=request.voice, speed=request.speed,
        )
        return TTSResponse(**result)
    except Exception as e:
        logger.exception("TTS synthesis failed")
        return JSONResponse(status_code=500, content={"detail": str(e)})


@voice_router.post("/voice/tts/stream")
async def tts_stream(request: TTSRequest):
    """Stream TTS audio chunks via SSE for long text."""
    mgr = _get_manager()
    if mgr is None or mgr.tts is None:
        return _unavailable("tts")

    async def event_generator():
        try:
            async for chunk in mgr.synthesize_streaming(
                request.text, voice=request.voice, speed=request.speed,
            ):
                yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: {\"type\": \"done\"}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(), media_type="text/event-stream",
    )


@voice_router.get("/voice/tts/voices", response_model=VoiceListResponse)
async def list_voices():
    """List available TTS voices."""
    mgr = _get_manager()
    if mgr is None or mgr.tts is None:
        return _unavailable("tts")

    voices = mgr.list_voices()
    return VoiceListResponse(
        voices=[VoiceInfo(**v) for v in voices],
    )


@voice_router.post(
    "/voice/tts/ensure-models", response_model=EnsureModelsResponse,
)
async def tts_ensure_models():
    """Ensure TTS model files are downloaded. Returns download status."""
    try:
        from lean_ai.voice.availability import is_tts_available
        if not is_tts_available():
            return _unavailable("tts")
    except ImportError:
        return _unavailable("tts")

    from lean_ai.voice.tts import (
        are_models_downloaded,
        ensure_models_downloaded,
        get_model_paths,
    )

    if are_models_downloaded():
        return EnsureModelsResponse(already_cached=True)

    try:
        await ensure_models_downloaded()
        model_path, voices_path = get_model_paths()
        size = os.path.getsize(model_path) + os.path.getsize(voices_path)
        return EnsureModelsResponse(
            downloaded=True, size_mb=round(size / (1024 * 1024), 1),
        )
    except Exception as e:
        logger.exception("TTS model download failed")
        return JSONResponse(
            status_code=500,
            content={"detail": f"Model download failed: {e}"},
        )


# ── Voice config ──


@voice_router.post("/voice/config")
async def voice_config(request: VoiceConfigRequest):
    """Update TTS voice/speed on the fly (runtime override, not persisted)."""
    from lean_ai.config import settings

    if request.voice:
        settings.tts_voice = request.voice
    if request.speed > 0:
        settings.tts_speed = request.speed

    return {
        "voice": settings.tts_voice,
        "speed": settings.tts_speed,
    }


# ── Wake word endpoints ──

# SSE connections waiting for wake word events
_wake_word_events: list[asyncio.Event] = []
_wake_word_lock = asyncio.Lock()


async def _on_wake_word_detected():
    """Callback when wake word is detected — notify all SSE listeners."""
    async with _wake_word_lock:
        for event in _wake_word_events:
            event.set()


@voice_router.post("/voice/wakeword/start")
async def wakeword_start():
    """Start background wake word listener."""
    mgr = _get_manager()
    if mgr is None or mgr.wake_word is None:
        return _unavailable("wake_word")

    try:
        await mgr.start_wake_word(_on_wake_word_detected)
        return {"status": "listening"}
    except Exception as e:
        logger.exception("Wake word start failed")
        return JSONResponse(status_code=500, content={"detail": str(e)})


@voice_router.post("/voice/wakeword/stop")
async def wakeword_stop():
    """Stop background wake word listener."""
    mgr = _get_manager()
    if mgr is None or mgr.wake_word is None:
        return _unavailable("wake_word")

    try:
        await mgr.stop_wake_word()
        return {"status": "stopped"}
    except Exception as e:
        logger.exception("Wake word stop failed")
        return JSONResponse(status_code=500, content={"detail": str(e)})


@voice_router.get("/voice/events")
async def voice_events():
    """SSE endpoint for wake word detection events.

    The extension keeps this connection open when wake word is enabled.
    Backend pushes wake_word_detected events here.
    """
    event = asyncio.Event()

    async with _wake_word_lock:
        _wake_word_events.append(event)

    async def event_generator():
        try:
            while True:
                await event.wait()
                event.clear()
                yield f"data: {json.dumps({'type': 'wake_word_detected'})}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            async with _wake_word_lock:
                if event in _wake_word_events:
                    _wake_word_events.remove(event)

    return StreamingResponse(
        event_generator(), media_type="text/event-stream",
    )


# ── Status endpoint ──


@voice_router.get("/voice/status", response_model=VoiceStatusResponse)
async def voice_status():
    """Feature availability + setup instructions."""
    try:
        from lean_ai.voice.availability import (
            get_setup_instructions,
        )
        from lean_ai.voice.availability import (
            voice_status as get_status,
        )
        status = get_status()
        status["setup"] = get_setup_instructions()
        return VoiceStatusResponse(**status)
    except ImportError:
        return VoiceStatusResponse(
            setup={"pip": 'pip install "lean-ai[voice]"'},
        )
