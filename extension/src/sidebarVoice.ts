/**
 * Voice / TTS helpers extracted from LeanAISidebarProvider.
 *
 * Every function receives a `VoiceContext` that exposes the subset of
 * provider state it needs so the class method bodies become thin
 * one-liner delegates.
 */

import * as vscode from "vscode";
import type { BackendClient } from "./backendClient";
import { addExtras } from "./backendInstaller";
import { restartBackend } from "./backendProcess";

// ── Context interface ────────────────────────────────────────────

export interface VoiceContext {
    client: BackendClient;
    postMessage(msg: Record<string, unknown>): void;

    // Mutable TTS pipeline state — lives on the provider instance
    ttsEnabled: boolean;
    wakeWordActive: boolean;
    ttsSentenceQueue: string[];
    ttsSpeaking: boolean;
    ttsCancelled: boolean;
    ttsAbortController: AbortController | undefined;
    ttsSentenceAccumulator: string;
    ttsInCodeFence: boolean;
}

// ── STT ──────────────────────────────────────────────────────────

export async function handleSttStart(
    ctx: VoiceContext,
    autoStop: boolean,
): Promise<void> {
    try {
        await ctx.client.sttStart(autoStop);
    } catch (err) {
        ctx.postMessage({ type: "reply", text: `STT start failed: ${err}`, cls: "msg-error" });
        ctx.postMessage({ type: "sttRecording", active: false });
    }
}

export async function handleSttStop(ctx: VoiceContext): Promise<void> {
    try {
        const result = await ctx.client.sttStop();
        const autoSubmit = vscode.workspace.getConfiguration("lean-ai")
            .get<boolean>("wakeWordAutoSubmit", false);
        ctx.postMessage({ type: "sttResult", text: result.text, autoSubmit });
    } catch (err) {
        ctx.postMessage({ type: "reply", text: `STT failed: ${err}`, cls: "msg-error" });
        ctx.postMessage({ type: "sttRecording", active: false });
    }
}

// ── Voice toggle (TTS / STT / wake word) ─────────────────────────

export async function handleVoiceToggle(
    ctx: VoiceContext,
    feature: string,
    enabled: boolean,
): Promise<void> {
    if (feature === "tts") {
        ctx.ttsEnabled = enabled;
    } else if (feature === "stt") {
        // STT toggle is UI-only (mic button visibility)
    } else if (feature === "wakeWord") {
        if (enabled) {
            try {
                await ctx.client.wakeWordStart();
                ctx.client.connectVoiceEvents(
                    () => ctx.postMessage({ type: "wakeWordDetected" }),
                    () => ctx.postMessage({ type: "sttAutoStopped" }),
                    () => {
                        ctx.wakeWordActive = false;
                        ctx.postMessage({ type: "reply", text: "Wake word listener stopped unexpectedly.", cls: "msg-error" });
                        ctx.postMessage({ type: "wakeWordStopped" });
                    },
                );
                ctx.wakeWordActive = true;
            } catch (err) {
                ctx.postMessage({ type: "reply", text: `Wake word failed: ${err}`, cls: "msg-error" });
            }
        } else {
            try {
                await ctx.client.wakeWordStop();
                ctx.client.disconnectVoiceEvents();
                ctx.wakeWordActive = false;
            } catch {
                // Best effort
            }
        }
    }
}

// ── TTS text cleaning ────────────────────────────────────────────

/** Strip markdown formatting, special chars, and non-prose content so TTS reads naturally. */
export function stripCodeForTts(text: string): string {
    // Remove fenced code blocks (```...```)
    let cleaned = text.replace(/```[\s\S]*?```/g, "");
    // Remove inline code (`...`)
    cleaned = cleaned.replace(/`[^`]+`/g, "");
    // Remove HTML tags (<br>, <div>, etc.)
    cleaned = cleaned.replace(/<[^>]+>/g, "");
    // Remove markdown image syntax ![alt](url) before link syntax
    cleaned = cleaned.replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1");
    // Remove markdown link syntax [text](url) -> keep text
    cleaned = cleaned.replace(/\[([^\]]*)\]\([^)]*\)/g, "$1");
    // Remove footnote references [^1], [^note]
    cleaned = cleaned.replace(/\[\^[^\]]*\]/g, "");
    // Remove raw URLs (https://..., http://..., www.)
    cleaned = cleaned.replace(/https?:\/\/\S+/g, "");
    cleaned = cleaned.replace(/www\.\S+/g, "");
    // Remove markdown bold/italic markers (* and _)
    cleaned = cleaned.replace(/[*_]{1,3}/g, "");
    // Remove markdown table pipes and heading markers
    cleaned = cleaned.replace(/[|#]/g, "");
    // Remove markdown horizontal rules (--- or ***)
    cleaned = cleaned.replace(/^[-*]{3,}\s*$/gm, "");
    // Remove blockquote markers
    cleaned = cleaned.replace(/^\s*>\s*/gm, "");
    // Remove bullet markers (-, +, numbered lists)
    cleaned = cleaned.replace(/^\s*[-+]\s+/gm, "");
    cleaned = cleaned.replace(/^\s*\d+\.\s+/gm, "");
    // Replace arrows with natural words
    cleaned = cleaned.replace(/-->/g, " to ");
    cleaned = cleaned.replace(/=>/g, " to ");
    cleaned = cleaned.replace(/->/g, " to ");
    cleaned = cleaned.replace(/<--/g, " from ");
    cleaned = cleaned.replace(/<-/g, " from ");
    // Replace & with "and"
    cleaned = cleaned.replace(/\s&\s/g, " and ");
    // Replace triple dots with a pause-friendly comma
    cleaned = cleaned.replace(/\.{3}/g, ",");
    // Remove remaining special characters that TTS reads literally
    cleaned = cleaned.replace(/[~^\\{}[\]<>]/g, "");
    // Remove emoji (Unicode emoji ranges)
    cleaned = cleaned.replace(/[\u{1F600}-\u{1F64F}\u{1F300}-\u{1F5FF}\u{1F680}-\u{1F6FF}\u{1F1E0}-\u{1F1FF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}\u{FE00}-\u{FE0F}\u{1F900}-\u{1F9FF}\u{1FA00}-\u{1FA6F}\u{1FA70}-\u{1FAFF}\u{200D}\u{20E3}\u{E0020}-\u{E007F}]/gu, "");
    // Collapse multiple blank lines/spaces into one space
    cleaned = cleaned.replace(/\n{2,}/g, " ");
    cleaned = cleaned.replace(/\s{2,}/g, " ");
    return cleaned.trim();
}

// ── TTS playback ─────────────────────────────────────────────────

/** Speak text aloud via TTS. Prefers raw PCM streaming for lower latency. */
export async function speakText(ctx: VoiceContext, text: string): Promise<void> {
    try {
        const cleaned = stripCodeForTts(text);
        if (!cleaned || ctx.ttsCancelled) { return; }

        // Create an AbortController so the stop button can kill the active fetch
        const ac = new AbortController();
        ctx.ttsAbortController = ac;

        // Always use PCM streaming for lower latency — falls back to WAV on error
        try {
            await ctx.client.ttsStreamPcm(cleaned, undefined, undefined, (pcmBase64, sampleRate) => {
                ctx.postMessage({ type: "ttsAudio", audio: pcmBase64, isPcm: true, sampleRate });
            }, ac.signal);
        } catch (e) {
            if (ac.signal.aborted) { return; } // User pressed stop — not an error
            // Fallback: WAV streaming or non-streaming
            if (cleaned.length < 500) {
                const result = await ctx.client.ttsSynthesize(cleaned, undefined, undefined, ac.signal);
                if (result.audio_base64) {
                    ctx.postMessage({ type: "ttsAudio", audio: result.audio_base64 });
                }
            } else {
                await ctx.client.ttsStream(cleaned, undefined, undefined, (chunk) => {
                    ctx.postMessage({ type: "ttsAudio", audio: chunk });
                }, ac.signal);
            }
        }
    } catch (err) {
        console.warn("[Lean AI] TTS failed:", err);
    }
}

/** Queue a sentence for sequential TTS playback. Fire-and-forget safe. */
export function speakSentence(ctx: VoiceContext, text: string): void {
    if (ctx.ttsCancelled) { return; }
    ctx.ttsSentenceQueue.push(text);
    if (!ctx.ttsSpeaking) {
        ctx.ttsCancelled = false;
        processTtsSentenceQueue(ctx);
    }
}

async function processTtsSentenceQueue(ctx: VoiceContext): Promise<void> {
    if (ctx.ttsSpeaking) { return; }
    ctx.ttsSpeaking = true;
    try {
        while (ctx.ttsSentenceQueue.length > 0 && !ctx.ttsCancelled) {
            const sentence = ctx.ttsSentenceQueue.shift()!;
            await speakText(ctx, sentence);
        }
    } finally {
        ctx.ttsSpeaking = false;
    }
}

// ── TTS token accumulator (streaming) ────────────────────────────

/** Feed a streaming token into the TTS sentence accumulator.
 *  Detects sentence boundaries and dispatches complete sentences to speakSentence(). */
export function feedTtsToken(ctx: VoiceContext, token: string): void {
    ctx.ttsSentenceAccumulator += token;

    // Track code fences — don't speak code blocks
    const fenceCount = (token.match(/```/g) || []).length;
    if (fenceCount % 2 !== 0) {
        ctx.ttsInCodeFence = !ctx.ttsInCodeFence;
    }
    if (ctx.ttsInCodeFence) { return; }

    // Check for sentence boundaries in the accumulated text
    const sentenceEndPattern = /(?<=[.!?])\s+/;
    const match = sentenceEndPattern.exec(ctx.ttsSentenceAccumulator);
    if (!match) { return; }

    // Extract all complete sentences
    const remaining = ctx.ttsSentenceAccumulator;
    let lastEnd = 0;
    const re = /(?<=[.!?])\s+/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(remaining)) !== null) {
        const sentence = remaining.slice(lastEnd, m.index).trim();
        if (sentence) {
            // Strip fenced code blocks and inline code
            const cleaned = stripCodeForTts(sentence);
            if (cleaned) { speakSentence(ctx, cleaned); }
        }
        lastEnd = m.index + m[0].length;
    }
    ctx.ttsSentenceAccumulator = remaining.slice(lastEnd);
}

/** Reset TTS accumulator state for a fresh streaming session. */
export function resetTtsAccumulator(ctx: VoiceContext): void {
    ctx.ttsSentenceAccumulator = "";
    ctx.ttsInCodeFence = false;
    ctx.ttsSentenceQueue = [];
    ctx.ttsSpeaking = false;
    ctx.ttsCancelled = false;
    if (ctx.ttsAbortController) {
        ctx.ttsAbortController.abort();
        ctx.ttsAbortController = undefined;
    }
    ctx.postMessage({ type: "ttsStopPlayback" });
    ctx.postMessage({ type: "ttsClearReplay" });
}

/** Kill the entire TTS pipeline — sentence queue + active stream. */
export function stopTts(ctx: VoiceContext): void {
    ctx.ttsCancelled = true;
    ctx.ttsSentenceQueue = [];
    ctx.ttsSpeaking = false;
    ctx.ttsSentenceAccumulator = "";
    ctx.ttsInCodeFence = false;
    if (ctx.ttsAbortController) {
        ctx.ttsAbortController.abort();
        ctx.ttsAbortController = undefined;
    }
}

// ── Voice availability & setup ───────────────────────────────────

/** Send voiceAvailable + voice list to the webview. */
export function sendVoiceAvailable(ctx: VoiceContext, setupNeeded = false): void {
    // Sync provider TTS state with backend availability
    if (ctx.client.ttsAvailable) {
        ctx.ttsEnabled = true;
    }

    ctx.postMessage({
        type: "voiceAvailable",
        stt: ctx.client.sttAvailable,
        tts: ctx.client.ttsAvailable,
        wakeWord: ctx.client.wakeWordAvailable,
        setupNeeded,
    });
    if (ctx.client.ttsAvailable) {
        const voiceConfig = vscode.workspace.getConfiguration("lean-ai");
        ctx.client.listVoices().then((voices) => {
            ctx.postMessage({
                type: "voiceVoices",
                voices,
                current: voiceConfig.get<string>("ttsVoice", "af_heart"),
            });
        }).catch(() => {});

        // Proactively download TTS model files if not yet cached
        (async () => {
            try {
                const result = await vscode.window.withProgress(
                    {
                        location: vscode.ProgressLocation.Notification,
                        title: "Downloading TTS models (~310MB)...",
                        cancellable: false,
                    },
                    () => ctx.client.ensureTtsModels(),
                );
                if (result.downloaded) {
                    vscode.window.showInformationMessage(
                        "TTS models downloaded. Voice is ready!",
                    );
                }
            } catch { /* best effort */ }
        })();
    }
}

/** Show voice setup instructions in the chat panel. */
export function showVoiceSetupInstructions(ctx: VoiceContext): void {
    const isLinux = process.platform === "linux";
    const isMac = process.platform === "darwin";
    const portaudioCmd = isLinux
        ? "sudo apt install portaudio19-dev"
        : isMac
            ? "brew install portaudio"
            : "Install portaudio for your platform";

    const instructions = [
        "**Voice Setup Instructions:**",
        "",
        "1. Install system dependency (needed for STT & wake word):",
        "```",
        portaudioCmd,
        "```",
        "",
        "2. Run the **Lean AI: Reinstall Backend** command from the",
        "   Command Palette, or manually run in the backend venv:",
        "```",
        "pip install lean-ai[voice]",
        "```",
        "",
        "3. Restart the backend",
    ].join("\n");

    ctx.postMessage({ type: "reply", text: instructions, cls: "msg-ai" });
}

// ── Startup voice probe ──────────────────────────────────────────

export interface VoiceStartupDeps {
    voiceCtx: VoiceContext;
    extensionContext: vscode.ExtensionContext;
    postMessage(msg: Record<string, unknown>): void;
    /** Called when backend is healthy and voice is ready — provider runs greet logic. */
    onReady(): void;
}

/**
 * Probe backend for voice capabilities on startup. Offers to install deps
 * if voice settings are enabled but unavailable. Called from resolveWebviewView.
 */
export async function probeVoiceOnStartup(deps: VoiceStartupDeps): Promise<void> {
    const { voiceCtx: vCtx, extensionContext } = deps;

    deps.postMessage({ type: "visionAvailable", available: vCtx.client.visionAvailable });

    // Pre-load STT model in background so first voice use is instant
    if (vCtx.client.sttAvailable) {
        vCtx.client.sttWarmup();
    }

    // Detect voice settings enabled but deps not installed
    const voiceConfig = vscode.workspace.getConfiguration("lean-ai");
    const sttWanted = voiceConfig.get<boolean>("enableStt", false);
    const ttsWanted = voiceConfig.get<boolean>("enableTts", false);
    const wakeWanted = voiceConfig.get<boolean>("enableWakeWord", false);
    const anyWanted = sttWanted || ttsWanted || wakeWanted;
    const anyAvailable = vCtx.client.sttAvailable
        || vCtx.client.ttsAvailable
        || vCtx.client.wakeWordAvailable;

    if (anyWanted && !anyAvailable) {
        // Voice settings enabled but deps missing — offer to install
        const choice = await vscode.window.showWarningMessage(
            "Voice features are enabled but dependencies are not installed.",
            "Install Now",
            "Show Instructions",
        );
        if (choice === "Install Now") {
            const ok = await addExtras(extensionContext, ["voice"]);
            if (ok) {
                vscode.window.showInformationMessage(
                    "Voice dependencies installed. Restarting backend...",
                );
                await restartBackend();
                // Re-probe after restart
                setTimeout(() => {
                    vCtx.client.healthCheck().then(() => {
                        sendVoiceAvailable(vCtx);
                    }).catch(() => {});
                }, 3000);
                return;
            } else {
                showVoiceSetupInstructions(vCtx);
            }
        } else if (choice === "Show Instructions") {
            showVoiceSetupInstructions(vCtx);
        }
        // Still missing — show hint in voice controls bar
        sendVoiceAvailable(vCtx, true);
        return;
    }

    sendVoiceAvailable(vCtx);

    // Mark setup complete — backend is healthy
    if (!extensionContext.globalState.get<boolean>("lean-ai.hasCompletedSetup")) {
        extensionContext.globalState.update("lean-ai.hasCompletedSetup", true);
    }

    deps.onReady();
}
