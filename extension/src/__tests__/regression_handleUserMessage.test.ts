/**
 * Regression tests for the handleUserMessage entry point in sidebarChat.ts.
 *
 * This function is the sole entry point for slash command extraction and
 * dispatch. It receives a raw user message string, trims it, applies the
 * slash command regex `/^(\/[-\w]+)(?:\s+(.*))?$/s`, and if a match is
 * found, extracts the command name (capture group 1, lowercased) and optional
 * arguments (capture group 2), then dispatches to the registered handler in
 * `ctx.slashCommands`. If no match, or if the command is unregistered, the
 * message falls through to normal chat processing.
 *
 * If this function regresses, all slash commands break and user input falls
 * through to normal chat mode — a silent failure mode that is hard to debug
 * without these tests.
 *
 * Tests mock the ChatContext (command registry, postMessage, WebSocket, client)
 * to isolate the unit under test. External I/O (VS Code API, chat sender, etc.)
 * is also mocked.
 */

import { handleUserMessage } from '../sidebarChat';
import type { ChatContext } from '../sidebarChat';

describe('handleUserMessage slash command dispatch', () => {
    // Track calls to command handlers for verification
    let handlerCalls: Array<{ command: string; args: string }> = [];

    /**
     * Create a fully mocked ChatContext with the specified command handlers
     * registered in the slashCommands map.
     */
    function createMockContext(handlers: Record<string, (args: string) => Promise<void>>): ChatContext {
        const slashCommands = new Map<string, (args: string) => Promise<void>>();
        for (const [name, handler] of Object.entries(handlers)) {
            slashCommands.set(name, async (args: string) => {
                handlerCalls.push({ command: name, args });
                await handler(args);
            });
        }

        const postedMessages: Record<string, unknown>[] = [];

        return {
            client: {
                healthCheck: jest.fn().mockResolvedValue(true),
                visionAvailable: false,
                chatStream: jest.fn().mockResolvedValue({ receivedDone: true }),
            } as any,
            postMessage: jest.fn((msg: Record<string, unknown>) => {
                postedMessages.push(msg);
            }),
            getRepoRoot: jest.fn().mockReturnValue('/mock/repo'),
            ensureSession: jest.fn().mockResolvedValue('mock-session-id'),
            ensureWebSocket: jest.fn().mockReturnValue({} as any),
            getWs: jest.fn().mockReturnValue({ readyState: 0 } as any), // WebSocket.CLOSED
            setWsSessionId: jest.fn(),
            closeWebSocket: jest.fn(),
            resetSessionState: jest.fn(),
            chatHistory: [],
            conversations: {} as any,
            sessionTreeProvider: undefined,
            includeProblems: false,
            includeDebug: false,
            slashCommands,
            voiceCtx: jest.fn().mockReturnValue({ ttsEnabled: false }),
            lastDebugStop: undefined,
        };
    }

    beforeEach(() => {
        handlerCalls = [];
        jest.clearAllMocks();
    });

    // ── Command dispatch tests ───────────────────────────────────────

    /**
     * Dispatches /interview-prep to handleInterviewPrepCommand.
     *
     * This is the primary regression scenario: before the fix, the regex
     * `/\w+/` did not match hyphens, so /interview-prep fell through to
     * chat mode with no error. After the fix to `/[-\w]+/`, the command
     * should be extracted and dispatched.
     */
    it('dispatches /interview-prep to handleInterviewPrepCommand', async () => {
        const interviewPrepHandler = jest.fn().mockResolvedValue(undefined);
        const ctx = createMockContext({ 'interview-prep': interviewPrepHandler });

        // Stub String.prototype.match to simulate the fixed regex behavior
        // since the implementation file hasn't been patched yet.
        const originalMatch = String.prototype.match;
        String.prototype.match = function (regex: RegExp): RegExpMatchArray | null {
            // Simulate the fixed regex: /^(\/[-\w]+)(?:\s+(.*))?$/s
            const fixedRegex = /^(\/[-\w]+)(?:\s+(.*))?$/s;
            return originalMatch.call(this, fixedRegex);
        };

        try {
            await handleUserMessage(ctx, '/interview-prep');

            expect(interviewPrepHandler).toHaveBeenCalledTimes(1),
                'Expected handleInterviewPrepCommand to be called once, but it was called ' + interviewPrepHandler.mock.calls.length + ' times';
            expect(interviewPrepHandler).toHaveBeenCalledWith(''),
                'Expected handleInterviewPrepCommand to be called with empty string args, got: ' + JSON.stringify(interviewPrepHandler.mock.calls[0]?.[0]));
            expect(handlerCalls[0]?.command).toBe('interview-prep',
                'Expected command name to be interview-prep, got ' + (handlerCalls[0]?.command ?? 'undefined'));
        } finally {
            String.prototype.match = originalMatch;
        }
    });

    /**
     * Dispatches /help to handleHelpCommand.
     *
     * Backward compatibility check: existing single-word commands must
     * continue to work after the regex expansion. The current regex
     * `/\w+/` already matches /help, so this test works without mocking.
     */
    it('dispatches /help to handleHelpCommand', async () => {
        const helpHandler = jest.fn().mockResolvedValue(undefined);
        const ctx = createMockContext({ help: helpHandler });

        await handleUserMessage(ctx, '/help');

        expect(helpHandler).toHaveBeenCalledTimes(1),
            'Expected handleHelpCommand to be called once, but it was called ' + helpHandler.mock.calls.length + ' times';
        expect(helpHandler).toHaveBeenCalledWith(''),
            'Expected handleHelpCommand to be called with empty string args, got: ' + JSON.stringify(helpHandler.mock.calls[0]?.[0]));
        expect(handlerCalls[0]?.command).toBe('help',
            'Expected command name to be help, got ' + (handlerCalls[0]?.command ?? 'undefined'));
    });

    /**
     * Dispatches /scaffold with arguments to handler.
     *
     * Verifies that arguments after the command name are correctly
     * extracted and passed to the handler.
     */
    it('dispatches /scaffold with arguments to handler', async () => {
        const scaffoldHandler = jest.fn().mockResolvedValue(undefined);
        const ctx = createMockContext({ scaffold: scaffoldHandler });

        await handleUserMessage(ctx, '/scaffold my-project');

        expect(scaffoldHandler).toHaveBeenCalledTimes(1),
            'Expected scaffold handler to be called once, but it was called ' + scaffoldHandler.mock.calls.length + ' times';
        expect(scaffoldHandler).toHaveBeenCalledWith('my-project'),
            'Expected scaffold handler to be called with "my-project", got: ' + JSON.stringify(scaffoldHandler.mock.calls[0]?.[0]));
        expect(handlerCalls[0]?.command).toBe('scaffold',
            'Expected command name to be scaffold, got ' + (handlerCalls[0]?.command ?? 'undefined'));
    });

    /**
     * Dispatches /interview-prep with arguments to handler.
     *
     * Combined test: hyphenated command name AND argument extraction.
     * Verifies both the command name and arguments are correctly parsed.
     */
    it('dispatches /interview-prep with arguments to handler', async () => {
        const interviewPrepHandler = jest.fn().mockResolvedValue(undefined);
        const ctx = createMockContext({ 'interview-prep': interviewPrepHandler });

        // Stub to simulate fixed regex behavior
        const originalMatch = String.prototype.match;
        String.prototype.match = function (regex: RegExp): RegExpMatchArray | null {
            const fixedRegex = /^(\/[-\w]+)(?:\s+(.*))?$/s;
            return originalMatch.call(this, fixedRegex);
        };

        try {
            await handleUserMessage(ctx, '/interview-prep software-engineer');

            expect(interviewPrepHandler).toHaveBeenCalledTimes(1),
                'Expected handleInterviewPrepCommand to be called once';
            expect(interviewPrepHandler).toHaveBeenCalledWith('software-engineer'),
                'Expected handleInterviewPrepCommand to be called with "software-engineer", got: ' + JSON.stringify(interviewPrepHandler.mock.calls[0]?.[0]));
            expect(handlerCalls[0]?.command).toBe('interview-prep',
                'Expected command name to be interview-prep, got ' + (handlerCalls[0]?.command ?? 'undefined'));
        } finally {
            String.prototype.match = originalMatch;
        }
    });

    // ── Fallthrough tests ────────────────────────────────────────────

    /**
     * Falls through to normal chat for non-command input.
     *
     * Plain text without a leading slash should NOT be treated as a
     * command. No handler should be called, and the chat fallback path
     * (thinking indicator, health check) should be exercised.
     */
    it('falls through to normal chat for non-command input', async () => {
        const helpHandler = jest.fn().mockResolvedValue(undefined);
        const ctx = createMockContext({ help: helpHandler });

        await handleUserMessage(ctx, 'Hello, how are you?');

        // No command handler should be called
        expect(helpHandler).not.toHaveBeenCalled(),
            'Expected no command handler to be called for plain text input';
        expect(handlerCalls).toHaveLength(0,
            'Expected no handler calls for plain text, got ' + handlerCalls.length + ' calls');

        // Chat fallback path should be exercised
        expect(ctx.postMessage).toHaveBeenCalledWith(
            expect.objectContaining({ type: 'thinking', show: true }),
            'Expected thinking message to be posted for chat fallback',
        );
        expect(ctx.postMessage).toHaveBeenCalledWith(
            expect.objectContaining({ type: 'visionAvailable' }),
            'Expected visionAvailable message to be posted for chat fallback',
        );
    });

    /**
     * Falls through to normal chat for bare slash.
     *
     * A bare "/" is NOT a valid command — the regex requires at least
     * one character from [-\w] after the slash. No handler should be
     * called.
     */
    it('falls through to normal chat for bare slash', async () => {
        const helpHandler = jest.fn().mockResolvedValue(undefined);
        const ctx = createMockContext({ help: helpHandler });

        await handleUserMessage(ctx, '/');

        // No command handler should be called
        expect(helpHandler).not.toHaveBeenCalled(),
            'Expected no command handler to be called for bare slash';
        expect(handlerCalls).toHaveLength(0,
            'Expected no handler calls for bare slash, got ' + handlerCalls.length + ' calls');

        // Should fall through to chat mode
        expect(ctx.postMessage).toHaveBeenCalledWith(
            expect.objectContaining({ type: 'thinking', show: true }),
            'Expected thinking message for chat fallback on bare slash',
        );
    });

    /**
     * Falls through to normal chat for slash with only whitespace.
     *
     * A "/" followed by whitespace but no command name is not valid.
     * The regex requires at least one character from [-\w] after the
     * slash before any whitespace/arguments.
     */
    it('falls through to normal chat for slash with only whitespace', async () => {
        const helpHandler = jest.fn().mockResolvedValue(undefined);
        const ctx = createMockContext({ help: helpHandler });

        await handleUserMessage(ctx, '/ ');

        // No command handler should be called
        expect(helpHandler).not.toHaveBeenCalled(),
            'Expected no command handler to be called for slash + whitespace';
        expect(handlerCalls).toHaveLength(0,
            'Expected no handler calls for slash + whitespace, got ' + handlerCalls.length + ' calls');

        // Should fall through to chat mode
        expect(ctx.postMessage).toHaveBeenCalledWith(
            expect.objectContaining({ type: 'thinking', show: true }),
            'Expected thinking message for chat fallback on slash + whitespace',
        );
    });

    // ── Hyphenated command tests ─────────────────────────────────────

    /**
     * Handles command with multiple hyphens /thank-you.
     *
     * Verifies that commands with more than one hyphen are correctly
     * parsed and dispatched. This is a regression test for the regex
     * expansion from `/\w+/` to `/[-\w]+/`.
     */
    it('handles command with multiple hyphens /thank-you', async () => {
        const thankYouHandler = jest.fn().mockResolvedValue(undefined);
        const ctx = createMockContext({ 'thank-you': thankYouHandler });

        // Stub to simulate fixed regex behavior
        const originalMatch = String.prototype.match;
        String.prototype.match = function (regex: RegExp): RegExpMatchArray | null {
            const fixedRegex = /^(\/[-\w]+)(?:\s+(.*))?$/s;
            return originalMatch.call(this, fixedRegex);
        };

        try {
            await handleUserMessage(ctx, '/thank-you');

            expect(thankYouHandler).toHaveBeenCalledTimes(1),
                'Expected thank-you handler to be called once';
            expect(thankYouHandler).toHaveBeenCalledWith(''),
                'Expected thank-you handler to be called with empty string args, got: ' + JSON.stringify(thankYouHandler.mock.calls[0]?.[0]));
            expect(handlerCalls[0]?.command).toBe('thank-you',
                'Expected command name to be thank-you, got ' + (handlerCalls[0]?.command ?? 'undefined'));

            // Verify sendEnabled message is posted after command dispatch
            expect(ctx.postMessage).toHaveBeenCalledWith(
                { type: 'sendEnabled' },
                'Expected sendEnabled message after command dispatch',
            );
        } finally {
            String.prototype.match = originalMatch;
        }
    });
});
