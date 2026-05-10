# Voice Interaction Design

> **Status:** Beta
>
> This document describes the voice interaction architecture in the `lean_ai` extension: Speech-to-Text (STT) via faster-whisper, Text-to-Speech (TTS) via kokoro-onnx, and wake word detection via openWakeWord. All three features run entirely on the local machine — no audio data leaves the user's device unless explicitly forwarded to a cloud LLM for transcription.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Speech-to-Text (STT)](#speech-to-text-stt)
4. [Text-to-Speech (TTS)](#text-to-speech-tts)
5. [Wake Word Detection](#wake-word-detection)
6. [Audio Manager and Microphone Coordination](#audio-manager-and-microphone-coordination)
7. [LLM Audio Transcription Fallback](#llm-audio-transcription-fallback)
8. [Provider Compatibility](#provider-compatibility)
9. [API Endpoints](#api-endpoints)
10. [Configuration](#configuration)
11. [Open Questions and Future Work](#open-questions-and-future-work)

---

## Overview

The `lean_ai` extension provides three voice features for hands-free coding:

- **Speech-to-Text (STT)** — Records from the microphone and transcribes locally using [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (a CTranslate2-based Whisper implementation).
- **Text-to-Speech (TTS)** — Reads LLM responses aloud using [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) with 58 voices across 9 languages.
- **Wake Word Detection** — Listens for "Hey Jarvis" using [openWakeWord](https://github.com/dscrippa/openWakeWord) so the user can trigger recording without touching the keyboard.

All processing is local. Audio capture uses PyAudio (portaudio) at 16 kHz mono 16-bit PCM. Features are enabled independently via environment variables or extension settings.

```
[Microphone] → [PyAudio (16kHz mono 16-bit PCM)]
  ├─→ [faster-whisper] → [Transcribed text] → [Chat input]
  ├─→ [openWakeWord] → [Wake word detected] → [Auto-start STT]
  └─→ [kokoro-onnx] ← [LLM response text] → [24kHz PCM audio] → [Speaker]
```

---

## Architecture

### Module Map

All voice code lives under `backend/src/lean_ai/voice/`:

| Module | Purpose |
|--------|---------|
| `audio_manager.py` | Singleton `AudioManager` that coordinates STT, TTS, and wake word services. Ensures only one service holds the microphone at a time. |
| `stt.py` | `STTService` — microphone capture via PyAudio and transcription via faster-whisper. |
| `tts.py` | `TTSService` — text-to-speech synthesis via kokoro-onnx with model auto-download. |
| `wake_word.py` | `WakeWordService` — background wake word listener using openWakeWord. |
| `availability.py` | Runtime availability checks (`is_stt_available`, `is_tts_available`, `is_wake_word_available`) and setup instructions. |
| `alsa_suppression.py` | Suppresses ALSA error messages on Linux during PyAudio initialization. |

The voice router (`backend/src/lean_ai/routers/voice.py`) exposes REST endpoints under `/voice/` that the extension frontend calls through `BackendVoiceClient`.

### Design Principles

- **Local-first:** All audio processing runs on the user's machine. No audio data is uploaded unless the user's flagged LLM role supports audio input (OpenAI, Gemini, Lean AI Serve).
- **Graceful degradation:** Each feature is independently optional. Missing dependencies result in 501 responses with setup instructions rather than crashes.
- **Microphone coordination:** The `AudioManager` uses an `asyncio.Lock` to prevent STT and wake word from competing for the microphone. The wake word listener pauses while STT is recording and resumes afterward.

---

## Speech-to-Text (STT)

### Implementation

STT is implemented in `STTService` (`backend/src/lean_ai/voice/stt.py`). It captures raw PCM audio from the microphone using PyAudio and transcribes using faster-whisper.

**Audio parameters:**

| Parameter | Value | Source |
|---------|-----|--------|
| Sample rate | 16000 Hz | `SAMPLE_RATE = 16000` |
| Channels | 1 (mono) | `CHANNELS = 1` |
| Chunk size | 1024 frames | `CHUNK_SIZE = 1024` |
| Format | 16-bit PCM (paInt16) | `FORMAT_WIDTH = 2` |

### Recording Pipeline

```
[User clicks mic button]
  → POST /voice/stt/start (auto_stop=true/false)
    → AudioManager.start_stt()
      → Pauses wake word listener (if running)
      → STTService.start_recording(auto_stop)
        → asyncio.run_in_executor(_recording_loop)
          → PyAudio stream reads 1024-frame chunks into _audio_buffer
          → If auto_stop: computes RMS per chunk, auto-stops after silence_threshold seconds
```

The recording loop runs in a background thread via `asyncio.run_in_executor`. Each chunk is appended to `self._audio_buffer` (a `list[bytes]`). When auto-stop is enabled, the loop computes RMS amplitude on each chunk and compares against `SILENCE_RMS_THRESHOLD = 500`. After `stt_silence_threshold` seconds of consecutive silence (default 4.0s), recording stops and the `_on_auto_stop` callback fires, which broadcasts an `stt_auto_stopped` SSE event.

### Transcription Pipeline

```
[User clicks mic again / auto-stop fires]
  → POST /voice/stt/stop
    → AudioManager.stop_stt()
      → STTService.stop_recording()
        → _transcribe_via_llm_or_whisper(audio_data)
          → Try flagged LLM handler first (resolve_audio_handler)
          → Fall back to faster-whisper on failure
        → Returns {text, language, duration_seconds}
      → Resumes wake word listener (if it was paused)
```

The transcription dispatcher (`_transcribe_via_llm_or_whisper`) first checks if any LLM role has audio support flagged via `resolve_audio_handler()`. If so, it wraps the raw PCM in a WAV container and sends it to that LLM client. On any failure (CapabilityError, network error, etc.), it falls back to the local faster-whisper model.

### Model Configuration

The faster-whisper model is lazy-loaded on first use. Pre-warming is available via `POST /voice/stt/warmup`.

| Setting | Default | Description |
|---------|---------|-------------|
| `LEAN_AI_STT_MODEL` | `turbo` | Whisper model: `tiny`, `base`, `small`, `medium`, `large-v3`, `turbo` |
| `LEAN_AI_STT_LANGUAGE` | *(empty, auto-detect)* | ISO 639-1 language code |
| `LEAN_AI_STT_SILENCE_THRESHOLD` | `4.0` | Seconds of silence before auto-stop |
| `LEAN_AI_STT_BEAM_SIZE` | `1` | 1=greedy (fastest), 5=beam search (most accurate) |
| `LEAN_AI_STT_CPU_THREADS` | `6` | CPU threads for faster-whisper inference |

The model always runs on CPU (GPU is reserved for the LLM/Ollama) using int8 compute type for efficiency.

---

## Text-to-Speech (TTS)

### Implementation

TTS is implemented in `TTSService` (`backend/src/lean_ai/voice/tts.py`). It uses kokoro-onnx to generate 24 kHz PCM audio, optionally wrapped in WAV containers, and returns base64-encoded chunks.

**Audio parameters:**

| Parameter | Value | Source |
|---------|-----|--------|
| Sample rate | 24000 Hz | `KOKORO_SAMPLE_RATE = 24000` |
| Output format | 16-bit PCM | `PCM_16` via soundfile |

### Model Management

Model files are auto-downloaded to `~/.cache/lean_ai/kokoro/` on first use (or `~/Library/Caches/lean_ai/kokoro/` on macOS). Three quality variants are available:

| Quality | Filename | Size | Setting value |
|---------|----------|------|--------------|
| FP32 | `kokoro-v1.0.onnx` | ~311 MB | `fp32` |
| FP16 | `kokoro-v1.0.fp16.onnx` | ~169 MB | `fp16` (default) |
| INT8 | `kokoro-v1.0.int8.onnx` | ~88 MB | `int8` |

A separate voices file (`voices-v1.0.bin`) is also downloaded. The endpoint `POST /voice/tts/ensure-models` triggers a download if models are missing and returns the cached size.

### Voices

Kokoro provides 58 voices across 9 languages. Voice IDs use a prefix convention where the first character indicates the language and the second indicates gender (f=male, f=female):

| Prefix | Language | BCP-47 |
|--------|----------|--------|
| `a` | American English | `en-us` |
| `b` | British English | `en-gb` |
| `e` | Spanish | `es` |
| `f` | French | `fr-fr` |
| `h` | Hindi | `hi` |
| `i` | Italian | `it` |
| `j` | Japanese | `ja` |
| `p` | Brazilian Portuguese | `pt-br` |
| `z` | Mandarin Chinese | `zh-cn` |

The default voice is `af_heart` (American English, female). Available voices are listed via `GET /voice/tts/voices`.

### Synthesis Modes

Three synthesis modes are supported:

1. **Batch synthesis** (`POST /voice/tts`) — Returns a single base64-encoded WAV with `audio_base64` and `duration_seconds`.
2. **WAV streaming** (`POST /voice/tts/stream`) — Streams base64 WAV chunks via Server-Sent Events (SSE). Each chunk includes a 44-byte WAV header.
3. **PCM streaming** (`POST /voice/tts/stream-pcm`) — Streams raw Int16 PCM samples via SSE with no WAV header overhead. Clients construct `AudioBuffer`s synchronously without `decodeAudioData()` overhead, enabling lower-latency playback.

The frontend (`extension/src/sidebarVoice.ts`) prefers PCM streaming for real-time playback and falls back to WAV streaming or batch synthesis.

### Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `LEAN_AI_TTS_VOICE` | `af_heart` | kokoro-onnx voice ID |
| `LEAN_AI_TTS_SPEED` | `1.0` | Speed multiplier (0.5–2.0) |
| `LEAN_AI_TTS_MODEL_QUALITY` | `fp16` | Model quality: `fp32`, `fp16`, `int8` |
| `LEAN_AI_TTS_CPU_THREADS` | `0` | ONNX intra-op threads (0 = auto: min(cpu_count, 8)) |

Runtime voice/speed changes are supported via `POST /voice/config` without restarting.

---

## Wake Word Detection

### Implementation

Wake word detection is implemented in `WakeWordService` (`backend/src/lean_ai/voice/wake_word.py`). It uses openWakeWord (v0.4+ API) to listen for the "Hey Jarvis" wake phrase on the microphone.

**Audio parameters:**

| Parameter | Value | Source |
|---------|-----|--------|
| Sample rate | 16000 Hz | `SAMPLE_RATE = 16000` |
| Channels | 1 (mono) | `CHANNELS = 1` |
| Chunk duration | 80 ms | `CHUNK_DURATION_MS = 80` |
| Chunk size | 1280 samples | `CHUNK_SIZE = 1280` |
| Confidence threshold | 0.5 | `CONFIDENCE_THRESHOLD = 0.5` |

### Listener Pipeline

```
[POST /voice/wakeword/start]
  → AudioManager.start_wake_word(callback, on_error)
    → WakeWordService.start(callback, on_error)
      → _listener_loop() in background thread
        → PyAudio stream reads 1280-sample frames
        → Model.predict(audio) returns scores for each wake word
        → If score for "hey_jarvis" > 0.5 → fires callback → SSE event "wake_word_detected"
```

The listener loop runs in a separate thread via `asyncio.run_in_executor`. When the wake word is detected, the `_on_wake_word_detected` callback broadcasts a `wake_word_detected` event through the SSE endpoint at `GET /voice/events`.

### SSE Event Channel

The extension maintains a persistent SSE connection to `GET /voice/events` when wake word is enabled. Events include:

| Event | Trigger |
|-------|---------|
| `wake_word_detected` | Wake word confidence exceeds threshold |
| `stt_auto_stopped` | STT recording auto-stopped due to silence |
| `wake_word_error` | Wake word listener encountered an error |

The SSE connection sends heartbeats every 30 seconds to keep the connection alive.

### Microphone Coordination

When the wake word triggers and STT starts recording, the `AudioManager.start_stt()` method pauses the wake word listener to release the microphone. After STT stops, `AudioManager.stop_stt()` resumes the wake word listener if it was previously active. This prevents both services from competing for the same audio device.

---

## Audio Manager and Microphone Coordination

The `AudioManager` singleton (`backend/src/lean_ai/voice/audio_manager.py`) orchestrates all voice services. It is created lazily on first call via `get_audio_manager()` and only instantiates services that pass their availability checks.

### Initialization

```python
mgr = get_audio_manager()  # Returns AudioManager or None
# mgr.stt      → STTService or None
# mgr.tts      → TTSService or None
# mgr.wake_word → WakeWordService or None
```

### Microphone Coordination

The `AudioManager` uses an `asyncio.Lock` (`self._lock`) to serialize access to voice services. This prevents STT and wake word from competing for the microphone:

1. When STT starts (`start_stt()`), the wake word listener is paused if running. The flag `_wake_word_was_active` tracks whether it was paused.
2. When STT stops (`stop_stt()`), the wake word listener is resumed if `_wake_word_was_active` is true, restoring the original callbacks.
3. Cleanup (`cleanup()`) releases all resources: STT releases the PyAudio stream and model, wake word releases its listener thread and model.

---

## LLM Audio Transcription Fallback

When a user records audio, the STT service doesn't just use faster-whisper — it first checks if any LLM role has audio input support flagged. This is handled by `resolve_audio_handler()` in `backend/src/lean_ai/routers/dependencies.py`.

### Resolution Priority

The handler checks roles in this order: **primary → request → worker → expert → inline**. For each role, it checks both the `supports_audio_*` flag and whether the client is actually configured.

### Supported Audio Providers

The `attach_audio` function in `backend/src/lean_ai/llm/media_messages.py` defines which providers accept audio:

| Provider | Audio Support | Content Block Shape |
|----------|--------------|----------------------|
| OpenAI | Yes | `{"type": "input_audio", "input_audio": {"data": b64, "format": "wav"}}` |
| Lean AI Serve | Yes | Same as OpenAI (uses `OpenAIProvider`) |
| Gemini | Yes | `{"type": "audio", "data": b64, "mime_type": "audio/wav"}` |
| Anthropic | No | Raises `CapabilityError` |
| Ollama | No | Raises `CapabilityError` |

When the LLM handler succeeds, the raw PCM is wrapped in a WAV container (16 kHz mono), base64-encoded, and sent with a transcribe-only system prompt. On any failure, the system falls back to faster-whisper.

---

## Provider Compatibility

### STT Transcription

| Provider | Direct Audio Input | Notes |
|----------|----|-------|
| OpenAI | Yes | GPT-4o and newer accept audio in chat completions. Also has `whisper-1` for standalone transcription. |
| Lean AI Serve | Yes | OpenAI-compatible API; accepts audio input blocks. |
| Gemini | Yes | Supports multimodal input including audio as inline data parts. |
| Anthropic | No | Text-only. Audio transcribed via faster-whisper fallback. |
| Ollama | No | Common Ollama models don't accept audio. Transcribed via faster-whisper fallback. |

### TTS Output

TTS is always local (kokoro-onnx) — no provider dependency. All providers benefit equally from text-to-speech output.

---

## API Endpoints

All voice endpoints are mounted under `/voice/` via the `voice_router` in `backend/src/lean_ai/routers/voice.py`.

### STT

| Method | Path | Description |
|--------|------|-------------|
| POST | `/voice/stt/start` | Start microphone recording. Body: `{auto_stop: bool}` |
| POST | `/voice/stt/stop` | Stop recording and return transcribed text. Returns `{text, language, duration_seconds}` |
| POST | `/voice/stt/warmup` | Pre-load the Whisper model in the background |

### TTS

| Method | Path | Description |
|--------|------|-------------|
| POST | `/voice/tts` | Batch synthesis. Returns `{audio_base64, duration_seconds}` |
| POST | `/voice/tts/stream` | Stream WAV chunks via SSE |
| POST | `/voice/tts/stream-pcm` | Stream raw PCM chunks via SSE (lower latency) |
| GET | `/voice/tts/voices` | List available voices |
| POST | `/voice/tts/ensure-models` | Download model files if missing |
| POST | `/voice/config` | Update TTS voice/speed at runtime |

### Wake Word

| Method | Path | Description |
|--------|------|-------------|
| POST | `/voice/wakeword/start` | Start background wake word listener |
| POST | `/voice/wakeword/stop` | Stop wake word listener |
| GET | `/voice/events` | SSE endpoint for voice events |

### Status

| Method | Path | Description |
|--------|------|-------------|
| GET | `/voice/status` | Feature availability and setup instructions |

When a feature is unavailable, endpoints return HTTP 501 with setup instructions including pip and system-level commands.

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LEAN_AI_ENABLE_STT` | `false` | Enable Speech-to-Text |
| `LEAN_AI_STT_MODEL` | `turbo` | Whisper model name |
| `LEAN_AI_STT_LANGUAGE` | *(empty)* | ISO 639-1 language code (empty = auto-detect) |
| `LEAN_AI_STT_SILENCE_THRESHOLD` | `4.0` | Seconds of silence before auto-stop |
| `LEAN_AI_STT_BEAM_SIZE` | `1` | Beam search size (1=greedy, 5=most accurate) |
| `LEAN_AI_STT_CPU_THREADS` | `6` | CPU threads for faster-whisper |
| `LEAN_AI_ENABLE_TTS` | `false` | Enable Text-to-Speech |
| `LEAN_AI_TTS_VOICE` | `af_heart` | kokoro-onnx voice ID |
| `LEAN_AI_TTS_SPEED` | `1.0` | Speed multiplier (0.5–2.0) |
| `LEAN_AI_TTS_MODEL_QUALITY` | `fp16` | Model quality: `fp32`, `fp16`, `int8` |
| `LEAN_AI_TTS_CPU_THREADS` | `0` | ONNX threads (0 = auto) |
| `LEAN_AI_ENABLE_WAKE_WORD` | `false` | Enable wake word detection |
| `LEAN_AI_SUPPORTS_AUDIO_PRIMARY` | `false` | Flag primary role for audio input |
| `LEAN_AI_SUPPORTS_AUDIO_REQUEST` | `false` | Flag request role for audio input |
| `LEAN_AI_SUPPORTS_AUDIO_WORKER` | `false` | Flag worker role for audio input |
| `LEAN_AI_SUPPORTS_AUDIO_EXPERT` | `false` | Flag expert role for audio input |
| `LEAN_AI_SUPPORTS_AUDIO_INLINE` | `false` | Flag inline role for audio input |

### Extension Settings

The `lean-ai.supportsAudio*` settings in the extension sync to the corresponding `LEAN_AI_SUPPORTS_AUDIO_*` environment variables via `extension/src/settingsSync.ts`.

### Installation

```bash
# Install voice dependencies (requires portaudio system library)
# Ubuntu/Debian:
sudo apt install portaudio19-dev
# macOS:
brew install portaudio

# Install Python voice extras
pip install "lean-ai[voice]"
```

When voice dependencies are missing but settings are enabled, the extension offers to install them automatically. The `/voice/status` endpoint returns per-feature setup instructions.

---

## Open Questions and Future Work

1. **Transcription Quality Metrics:** Should we expose confidence scores or word-level timestamps from faster-whisper to the user?
2. **Real-Time Streaming Transcription:** Can we stream partial transcripts to the chat as the user speaks, rather than waiting for full transcription?
3. **Custom Wake Words:** Should we allow users to train or select custom wake words beyond "Hey Jarvis"?
4. **Noise Suppression:** Should we integrate a client-side noise suppression filter (e.g., RNNoise) before transcription?
5. **Recording Persistence:** Should recordings be saved locally as a fallback before transcription, in case the transcription fails?
6. **TTS Caching:** Should frequently spoken responses be cached to reduce synthesis latency?
7. **Multi-Microphone Support:** Should we allow selecting a specific input device when multiple microphones are available?

---

## References

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — CTranslate2-based Whisper implementation
- [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) — ONNX-based text-to-speech with 58 voices
- [openWakeWord](https://github.com/dscrippa/openWakeWord) — Open-source wake word detection
- [PyAudio](https://people.csail.mit.edu/hubert/pyaudio/) — Python bindings for PortAudio
- [OpenAI Audio API](https://platform.openai.com/docs/guides/audio) — Audio input for chat completions
- [Gemini Multimodal Input](https://ai.google.dev/gemini-api/docs/multimodal-inputs) — Audio support in Gemini API
