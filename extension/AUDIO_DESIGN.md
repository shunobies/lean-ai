# Audio Capture Workflow Design

> **Status:** Experimental / Beta
>
> This document describes the current audio capture workflow in the `lean_ai` extension, provider compatibility constraints, chunking strategies for large transcripts, and proposed improvements for end-of-speech detection. Audio-related settings are marked **beta** because not all providers support audio input.

---

## Table of Contents

1. [Overview](#overview)
2. [Current State](#current-state)
3. [Provider Compatibility](#provider-compatibility)
4. [Chunking Strategies](#chunking-strategies)
5. [End-of-Speech Detection](#end-of-speech-detection)
6. [Configuration and Beta Warnings](#configuration-and-beta-warnings)
7. [Open Questions and Future Work](#open-questions-and-future-work)

---

## Overview

The `lean_ai` extension supports audio capture as an input modality alongside text. Users can record voice input that is transcribed and forwarded to the active AI provider. Because audio support depends on provider capabilities and platform APIs, this feature is flagged as **beta** and guarded behind explicit configuration toggles.

The audio capture workflow has four phases:

1. **Capture** — The user initiates recording; the extension collects raw audio frames from the microphone.
2. **Transcription** — Audio frames are sent to a transcription service (provider-dependent).
3. **Chunking** — If the transcript exceeds token limits, it is split into manageable chunks.
4. **Submission** — The final transcript (or chunk sequence) is submitted to the chat provider.

---

## Current State

### Capture Pipeline

The current implementation follows this flow:

```
[User presses record] → [Browser MediaRecorder API] → [WebM/Opus audio blob]
  → [Transcription service call] → [Plain-text transcript] → [Chat input]
```

- **Recording format:** WebM container with Opus codec (browser default via `MediaRecorder`).
- **Trigger:** A toolbar button in the VS Code webview toggles recording on/off.
- **Transcription:** Delegated to the provider's native audio support or a separate transcription endpoint when available.

### Limitations

- Not all providers accept audio directly. Some require a separate transcription step before the text is sent to the chat model.
- Long recordings produce large blobs that may exceed API payload limits.
- No client-side voice activity detection (VAD) — the user must manually stop recording.

---

## Provider Compatibility

### Supported Providers

| Provider | Direct Audio | Transcription Endpoint | Notes |
|----------|-------------|------------------------|-------|
| OpenAI (GPT-4o) | Yes | Yes (whisper) | Best-in-class audio support; accepts audio natively in chat completions |
| Anthropic (Claude) | No | No | Text-only; audio must be transcribed externally before submission |
| Google (Gemini) | Yes | Yes | Supports multimodal input including audio |
| Local / Ollama | No | No (unless whisper model loaded) | Depends on locally loaded models; whisper available as separate model |

### Compatibility Matrix Details

- **OpenAI:** GPT-4o and newer models support audio input directly in the `chat.completions` API. The `whisper-1` model is available for standalone transcription when the chat model does not accept audio.
- **Anthropic:** Claude models are text-only. Audio capture with Anthropic requires an external transcription step (e.g., OpenAI Whisper) before the transcript is sent to the chat endpoint.
- **Google Gemini:** Supports multimodal input, including audio, in the generative model API. Audio is passed as an inline data part.
- **Local/Ollama:** Audio support is model-dependent. If a Whisper model is loaded in Ollama, transcription is possible. Otherwise, audio capture is not functional.

### Beta Warning Placement

All audio-related settings display a **beta** badge in the VS Code settings UI. The setting descriptions include:

> ⚠️ **Beta:** Audio capture requires provider support. Not all models accept audio input. Check your provider's compatibility before enabling.

---

## Chunking Strategies

When transcripts exceed provider token limits, the extension must split the content into chunks. Three strategies are evaluated:

### Strategy A: Fixed-Size Chunking

Split the transcript into fixed character or token counts (e.g., 4000 tokens per chunk).

- **Pros:** Simple to implement; predictable chunk sizes.
- **Cons:** May split sentences or semantic units mid-thought; loses context at chunk boundaries.
- **Best for:** Short-form voice notes where semantic coherence is less critical.

### Strategy B: Semantic Chunking

Split the transcript at natural boundaries (paragraph breaks, speaker turns, topic shifts).

- **Pros:** Preserves meaning within each chunk; easier for the model to process coherent segments.
- **Cons:** Requires NLP analysis to detect boundaries; variable chunk sizes may still exceed limits.
- **Best for:** Meeting transcripts, interviews, or structured conversations.

### Strategy C: Sliding-Window Chunking

Use overlapping windows (e.g., 4000-token windows with 500-token overlap) to preserve context across boundaries.

- **Pros:** Maintains continuity; no context loss at boundaries.
- **Cons:** Higher token usage due to overlap; more API calls for the same content.
- **Best for:** Long-form content where context retention is critical.

### Recommended Default

**Strategy B (Semantic Chunking)** with a fallback to **Strategy A (Fixed-Size)** when semantic boundaries cannot be detected. The overlap from Strategy C is applied only when explicitly configured by the user.

### Chunking Configuration

```jsonc
// In settings.json
{
  "lean_ai.audio.chunkStrategy": "semantic",       // "fixed" | "semantic" | "sliding"
  "lean_ai.audio.maxChunkTokens": 4000,            // Max tokens per chunk
  "lean_ai.audio.overlapTokens": 500               // Overlap for sliding window (beta)
}
```

---

## End-of-Speech Detection

### Current Behavior

Recording is manually controlled: the user presses a button to start and stop. There is no automatic detection of speech boundaries.

### Proposed Improvements

#### 1. Client-Side Voice Activity Detection (VAD)

Integrate a lightweight VAD model (e.g., `silero-vad` via WebAssembly or a WebWorker) to detect when the user stops speaking.

- **Behavior:** Recording starts on first detected speech and stops after a configurable silence threshold (default: 2 seconds).
- **Configuration:**
  ```jsonc
  "lean_ai.audio.vadEnabled": true,           // Enable automatic VAD (beta)
  "lean_ai.audio.silenceThresholdMs": 2000,   // Silence duration before auto-stop
  "lean_ai.audio.vadSensitivity": "medium"    // "low" | "medium" | "high"
  ```

#### 2. Push-to-Talk Mode

A keyboard shortcut or mouse-hold mode for explicit control, useful in noisy environments where VAD is unreliable.

- **Behavior:** Recording is active only while the key/button is held.
- **Configuration:**
  ```jsonc
  "lean_ai.audio.pushToTalk": false,          // Use push-to-talk instead of VAD
  "lean_ai.audio.pushToTalkKey": "Ctrl+M"     // Keybinding for push-to-talk
  ```

#### 3. Combined Mode (VAD + Manual Override)

VAD handles the common case of automatic stop, but the user can still press the button to stop early or extend recording past the silence threshold.

- **Behavior:** VAD triggers auto-stop after silence, but a manual button press overrides at any time.
- **Configuration:**
  ```jsonc
  "lean_ai.audio.stopMode": "combined"        // "manual" | "vad" | "combined"
  ```

### Detection Pipeline

```
[Audio stream] → [VAD model (WebWorker)] → [Speech/Silence events]
  → [Silence timer] → [Auto-stop after threshold] → [Transcription]
```

The VAD model runs entirely client-side to avoid sending raw audio to any server before the user is ready. This preserves privacy and reduces bandwidth.

---

## Configuration and Beta Warnings

### Settings Schema (Audio Section)

All audio settings are grouped under `lean_ai.audio` and carry the `tags: ["experimental"]` annotation in the VS Code settings contribution.

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `audio.enabled` | boolean | `false` | Enable audio capture (beta) |
| `audio.provider` | string | `"auto"` | Transcription provider: `"auto"`, `"openai"`, `"local"` |
| `audio.chunkStrategy` | string | `"semantic"` | Chunking strategy for long transcripts |
| `audio.maxChunkTokens` | number | `4000` | Maximum tokens per chunk |
| `audio.overlapTokens` | number | `500` | Overlap tokens for sliding window |
| `audio.vadEnabled` | boolean | `false` | Enable client-side voice activity detection |
| `audio.silenceThresholdMs` | number | `2000` | Silence duration before auto-stop |
| `audio.vadSensitivity` | string | `"medium"` | VAD sensitivity level |
| `audio.pushToTalk` | boolean | `false` | Use push-to-talk mode |
| `audio.pushToTalkKey` | string | `"Ctrl+M"` | Keybinding for push-to-talk |
| `audio.stopMode` | string | `"manual"` | Stop mode: `"manual"`, `"vad"`, `"combined"` |

### UI Warnings

The settings UI displays a warning banner when audio is enabled but the current provider does not support audio input:

> ⚠️ **Provider Warning:** Your current provider ([Provider Name]) does not support audio input. Transcripts will be sent as text. Consider switching to a provider with audio support or enabling an external transcription service.

---

## Open Questions and Future Work

1. **Transcription Quality:** How does transcription accuracy vary across providers and accents? Should we expose a confidence score to the user?
2. **Real-Time Streaming:** Can we stream partial transcripts to the chat as the user speaks, rather than waiting for full transcription?
3. **Multi-Language Support:** Should the extension detect language automatically or let the user specify it for transcription?
4. **Noise Cancellation:** Should we integrate a client-side noise suppression filter before sending audio to the transcription service?
5. **Recording Persistence:** Should recordings be saved locally as a fallback before transcription, in case the transcription fails?

---

## References

- [OpenAI Audio API Documentation](https://platform.openai.com/docs/guides/audio)
- [Google Gemini Multimodal Input](https://ai.google.dev/gemini-api/docs/multimodal-inputs)
- [Silero VAD Model](https://github.com/snakers4/silero-vad)
- [Web MediaRecorder API](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder)
