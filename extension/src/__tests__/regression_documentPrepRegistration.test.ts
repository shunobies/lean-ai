/**
 * Regression test for /document-prep command registration in slashCommands.ts
 * 
 * This test verifies that the 'document-prep' map key exists and is properly dispatchable.
 * Command registration is load-bearing; removing this entry breaks slash command discovery.
 * 
 * @see extension/src/slashCommands.ts - The createSlashCommands factory builds the command map
 */

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

describe('handleUserMessage /document-prep command dispatch', () => {
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

    it('dispatches /document-prep to handleDocumentPrepCommand', async () => {
        const documentPrepHandler = jest.fn().mockResolvedValue(undefined);
        const ctx = createMockContext({ 'document-prep': documentPrepHandler });

        await handleUserMessage(ctx, '/document-prep');

        expect(documentPrepHandler).toHaveBeenCalledTimes(1);
        expect(documentPrepHandler).toHaveBeenCalledWith('');
        expect(handlerCalls[0]?.command).toBe('document-prep');
    });

    it('dispatches /document-prep with arguments to handler', async () => {
        const documentPrepHandler = jest.fn().mockResolvedValue(undefined);
        const ctx = createMockContext({ 'document-prep': documentPrepHandler });

        await handleUserMessage(ctx, '/document-prep essay 2000');

        expect(documentPrepHandler).toHaveBeenCalledTimes(1);
        expect(documentPrepHandler).toHaveBeenCalledWith('essay 2000');
        expect(handlerCalls[0]?.command).toBe('document-prep');
    });
});
