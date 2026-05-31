jest.mock(
    'vscode',
    () => ({
        window: {},
        workspace: {},
        commands: {},
    }),
    { virtual: true },
);

import { handleUserMessage } from '../sidebarChat';
import type { ChatContext } from '../sidebarChat';

describe('handleUserMessage slash command dispatch', () => {
    let handlerCalls: Array<{ command: string; args: string }> = [];

    function createMockContext(
        handlers: Record<string, (args: string) => Promise<void>>,
    ): ChatContext {
        const slashCommands = new Map<string, (args: string) => Promise<void>>();
        for (const [name, handler] of Object.entries(handlers)) {
            const commandName = name.startsWith('/') ? name : `/${name}`;
            slashCommands.set(commandName, async (args: string) => {
                handlerCalls.push({ command: name, args });
                await handler(args);
            });
        }

        return {
            client: {
                healthCheck: jest.fn().mockResolvedValue(true),
                visionAvailable: false,
                chatStream: jest.fn().mockResolvedValue({ receivedDone: true }),
            } as any,
            postMessage: jest.fn(),
            getRepoRoot: jest.fn().mockReturnValue('/mock/repo'),
            ensureSession: jest.fn().mockResolvedValue('mock-session-id'),
            ensureWebSocket: jest.fn().mockReturnValue({} as any),
            getWs: jest.fn().mockReturnValue({ readyState: 0 } as any),
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

    it('dispatches /interview-prep to handleInterviewPrepCommand', async () => {
        const interviewPrepHandler = jest.fn().mockResolvedValue(undefined);
        const ctx = createMockContext({ 'interview-prep': interviewPrepHandler });

        await handleUserMessage(ctx, '/interview-prep');

        expect(interviewPrepHandler).toHaveBeenCalledTimes(1);
        expect(interviewPrepHandler).toHaveBeenCalledWith('');
        expect(handlerCalls[0]?.command).toBe('interview-prep');
    });

    it('dispatches /help to handleHelpCommand', async () => {
        const helpHandler = jest.fn().mockResolvedValue(undefined);
        const ctx = createMockContext({ help: helpHandler });

        await handleUserMessage(ctx, '/help');

        expect(helpHandler).toHaveBeenCalledTimes(1);
        expect(helpHandler).toHaveBeenCalledWith('');
        expect(handlerCalls[0]?.command).toBe('help');
    });

    it('dispatches /scaffold with arguments to handler', async () => {
        const scaffoldHandler = jest.fn().mockResolvedValue(undefined);
        const ctx = createMockContext({ scaffold: scaffoldHandler });

        await handleUserMessage(ctx, '/scaffold my-project');

        expect(scaffoldHandler).toHaveBeenCalledTimes(1);
        expect(scaffoldHandler).toHaveBeenCalledWith('my-project');
        expect(handlerCalls[0]?.command).toBe('scaffold');
    });

    it('dispatches /interview-prep with arguments to handler', async () => {
        const interviewPrepHandler = jest.fn().mockResolvedValue(undefined);
        const ctx = createMockContext({ 'interview-prep': interviewPrepHandler });

        await handleUserMessage(ctx, '/interview-prep software-engineer');

        expect(interviewPrepHandler).toHaveBeenCalledTimes(1);
        expect(interviewPrepHandler).toHaveBeenCalledWith('software-engineer');
        expect(handlerCalls[0]?.command).toBe('interview-prep');
    });

    it('falls through to normal chat for non-command input', async () => {
        const helpHandler = jest.fn().mockResolvedValue(undefined);
        const ctx = createMockContext({ help: helpHandler });

        await handleUserMessage(ctx, 'Hello, how are you?');

        expect(helpHandler).not.toHaveBeenCalled();
        expect(handlerCalls).toHaveLength(0);
        expect(ctx.postMessage).toHaveBeenCalledWith(
            expect.objectContaining({ type: 'thinking', show: true }),
        );
        expect(ctx.postMessage).toHaveBeenCalledWith(
            expect.objectContaining({ type: 'visionAvailable' }),
        );
    });

    it('falls through to normal chat for bare slash', async () => {
        const helpHandler = jest.fn().mockResolvedValue(undefined);
        const ctx = createMockContext({ help: helpHandler });

        await handleUserMessage(ctx, '/');

        expect(helpHandler).not.toHaveBeenCalled();
        expect(handlerCalls).toHaveLength(0);
        expect(ctx.postMessage).toHaveBeenCalledWith(
            expect.objectContaining({ type: 'thinking', show: true }),
        );
    });

    it('falls through to normal chat for slash with only whitespace', async () => {
        const helpHandler = jest.fn().mockResolvedValue(undefined);
        const ctx = createMockContext({ help: helpHandler });

        await handleUserMessage(ctx, '/ ');

        expect(helpHandler).not.toHaveBeenCalled();
        expect(handlerCalls).toHaveLength(0);
        expect(ctx.postMessage).toHaveBeenCalledWith(
            expect.objectContaining({ type: 'thinking', show: true }),
        );
    });

    it('handles command with multiple hyphens /thank-you', async () => {
        const thankYouHandler = jest.fn().mockResolvedValue(undefined);
        const ctx = createMockContext({ 'thank-you': thankYouHandler });

        await handleUserMessage(ctx, '/thank-you');

        expect(thankYouHandler).toHaveBeenCalledTimes(1);
        expect(thankYouHandler).toHaveBeenCalledWith('');
        expect(handlerCalls[0]?.command).toBe('thank-you');
        expect(ctx.postMessage).toHaveBeenCalledWith({ type: 'sendEnabled' });
    });
});
