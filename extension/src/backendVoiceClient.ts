/**
 * Voice / STT / TTS / Wake Word client functions.
 *
 * Standalone functions that receive `baseUrl` (and any other needed params)
 * so they can be called from the main BackendClient without coupling.
 */

import * as http from "http";
import * as https from "https";
import { URL } from "url";

// ---------------------------------------------------------------------------
// STT
// ---------------------------------------------------------------------------

export async function sttWarmup(baseUrl: string): Promise<void> {
    try {
        await fetch(`${baseUrl}/api/voice/stt/warmup`, { method: "POST" });
    } catch { /* fire-and-forget */ }
}

export async function sttStart(baseUrl: string, autoStop = false): Promise<void> {
    const resp = await fetch(`${baseUrl}/api/voice/stt/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ auto_stop: autoStop }),
    });
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText })) as { detail?: string };
        throw new Error(err.detail ?? resp.statusText);
    }
}

export async function sttStop(baseUrl: string): Promise<{ text: string; language?: string; duration_seconds: number }> {
    const resp = await fetch(`${baseUrl}/api/voice/stt/stop`, {
        method: "POST",
    });
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText })) as { detail?: string };
        throw new Error(err.detail ?? resp.statusText);
    }
    return resp.json() as Promise<{ text: string; language?: string; duration_seconds: number }>;
}

// ---------------------------------------------------------------------------
// TTS
// ---------------------------------------------------------------------------

export async function ttsSynthesize(
    baseUrl: string,
    text: string,
    voice?: string,
    speed?: number,
    signal?: AbortSignal,
): Promise<{ audio_base64: string; duration_seconds: number }> {
    const resp = await fetch(`${baseUrl}/api/voice/tts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, voice: voice || "", speed: speed || 0 }),
        signal,
    });
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText })) as { detail?: string };
        throw new Error(err.detail ?? resp.statusText);
    }
    return resp.json() as Promise<{ audio_base64: string; duration_seconds: number }>;
}

export async function ttsStream(
    baseUrl: string,
    text: string,
    voice: string | undefined,
    speed: number | undefined,
    onChunk: (base64: string) => void,
    signal?: AbortSignal,
): Promise<void> {
    const resp = await fetch(`${baseUrl}/api/voice/tts/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, voice: voice || "", speed: speed || 0 }),
        signal,
    });
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText })) as { detail?: string };
        throw new Error(err.detail ?? resp.statusText);
    }
    const reader = resp.body?.getReader();
    if (!reader) { return; }
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
        const { done, value } = await reader.read();
        if (done) { break; }
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
            if (line.startsWith("data: ")) {
                try {
                    const data = JSON.parse(line.slice(6)) as { type?: string; audio_base64?: string };
                    if (data.type === "done") { return; }
                    if (data.audio_base64) { onChunk(data.audio_base64); }
                } catch { /* skip malformed */ }
            }
        }
    }
}

export async function ttsStreamPcm(
    baseUrl: string,
    text: string,
    voice: string | undefined,
    speed: number | undefined,
    onChunk: (pcmBase64: string, sampleRate: number) => void,
    signal?: AbortSignal,
): Promise<void> {
    const resp = await fetch(`${baseUrl}/api/voice/tts/stream-pcm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, voice: voice || "", speed: speed || 0 }),
        signal,
    });
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText })) as { detail?: string };
        throw new Error(err.detail ?? resp.statusText);
    }
    const reader = resp.body?.getReader();
    if (!reader) { return; }
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
        const { done, value } = await reader.read();
        if (done) { break; }
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
            if (line.startsWith("data: ")) {
                try {
                    const data = JSON.parse(line.slice(6)) as {
                        type?: string; pcm_base64?: string; sample_rate?: number;
                    };
                    if (data.type === "done") { return; }
                    if (data.pcm_base64 && data.sample_rate) {
                        onChunk(data.pcm_base64, data.sample_rate);
                    }
                } catch { /* skip malformed */ }
            }
        }
    }
}

export async function listVoices(baseUrl: string): Promise<Array<{ id: string; name: string; language: string; gender?: string }>> {
    const resp = await fetch(`${baseUrl}/api/voice/tts/voices`);
    if (!resp.ok) { return []; }
    const data = await resp.json() as { voices: Array<{ id: string; name: string; language: string; gender?: string }> };
    return data.voices || [];
}

export async function ensureTtsModels(baseUrl: string): Promise<{ downloaded: boolean; size_mb: number }> {
    const resp = await fetch(`${baseUrl}/api/voice/tts/ensure-models`, {
        method: "POST",
    });
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText })) as { detail?: string };
        throw new Error(err.detail ?? resp.statusText);
    }
    return resp.json() as Promise<{ downloaded: boolean; size_mb: number }>;
}

export async function voiceConfig(baseUrl: string, voice?: string, speed?: number): Promise<void> {
    await fetch(`${baseUrl}/api/voice/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ voice: voice || "", speed: speed || 0 }),
    });
}

// ---------------------------------------------------------------------------
// Wake Word
// ---------------------------------------------------------------------------

export async function wakeWordStart(baseUrl: string): Promise<void> {
    const resp = await fetch(`${baseUrl}/api/voice/wakeword/start`, {
        method: "POST",
    });
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText })) as { detail?: string };
        throw new Error(err.detail ?? resp.statusText);
    }
}

export async function wakeWordStop(baseUrl: string): Promise<void> {
    const resp = await fetch(`${baseUrl}/api/voice/wakeword/stop`, {
        method: "POST",
    });
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText })) as { detail?: string };
        throw new Error(err.detail ?? resp.statusText);
    }
}

// ---------------------------------------------------------------------------
// Voice Events SSE
// ---------------------------------------------------------------------------

/**
 * Mutable state object for the voice events SSE connection.
 * The caller owns the instance; `connectVoiceEvents` / `disconnectVoiceEvents`
 * read and write through it.
 */
export interface VoiceEventState {
    req: http.ClientRequest | null;
    reconnectTimer: ReturnType<typeof setTimeout> | null;
}

export function createVoiceEventState(): VoiceEventState {
    return { req: null, reconnectTimer: null };
}

export function connectVoiceEvents(
    baseUrl: string,
    state: VoiceEventState,
    onWakeWord: () => void,
    onSttAutoStop?: () => void,
    onError?: () => void,
): void {
    disconnectVoiceEvents(state);

    const connect = () => {
        const fullUrl = new URL(`${baseUrl}/api/voice/events`);
        const isHttps = fullUrl.protocol === "https:";
        const transport = isHttps ? https : http;

        const options: http.RequestOptions = {
            hostname: fullUrl.hostname,
            port: fullUrl.port || (isHttps ? "443" : "80"),
            path: fullUrl.pathname,
            method: "GET",
            timeout: 0,
        };

        let buffer = "";

        const req = transport.request(options, (res) => {
            if (res.statusCode && (res.statusCode < 200 || res.statusCode >= 300)) {
                console.error(`[Lean AI] Voice events SSE: HTTP ${res.statusCode}`);
                scheduleReconnect();
                return;
            }

            console.log("[Lean AI] Voice events SSE: connected");

            res.on("data", (chunk: Buffer | string) => {
                buffer += chunk.toString();
                const lines = buffer.split("\n");
                buffer = lines.pop()!;

                for (const line of lines) {
                    if (line.startsWith(":") || line === "") { continue; }
                    if (!line.startsWith("data: ")) { continue; }
                    try {
                        const data = JSON.parse(line.slice(6)) as { type?: string };
                        if (data.type === "wake_word_detected") {
                            onWakeWord();
                        } else if (data.type === "stt_auto_stopped" && onSttAutoStop) {
                            onSttAutoStop();
                        } else if (data.type === "wake_word_error" && onError) {
                            onError();
                        }
                    } catch { /* skip malformed SSE lines */ }
                }
            });

            res.on("end", () => {
                console.warn("[Lean AI] Voice events SSE: connection ended, reconnecting...");
                scheduleReconnect();
            });

            res.on("error", (err) => {
                console.error("[Lean AI] Voice events SSE: stream error:", err.message);
                scheduleReconnect();
            });
        });

        req.on("socket", (socket) => { socket.setTimeout(0); });
        req.on("error", (err) => {
            console.error("[Lean AI] Voice events SSE: request error:", err.message);
            scheduleReconnect();
        });
        req.end();

        state.req = req;
    };

    const scheduleReconnect = () => {
        if (state.req === null && state.reconnectTimer === null) { return; }
        state.req = null;
        state.reconnectTimer = setTimeout(() => {
            state.reconnectTimer = null;
            connect();
        }, 3000);
    };

    connect();
}

export function disconnectVoiceEvents(state: VoiceEventState): void {
    if (state.reconnectTimer !== null) {
        clearTimeout(state.reconnectTimer);
        state.reconnectTimer = null;
    }
    const req = state.req;
    state.req = null;
    if (req) {
        req.destroy();
    }
}
