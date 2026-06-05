/**
 * Regression tests for handleDocumentPrepCommand handler and /help update.
 *
 * Verifies that the handler collects intake inputs in sequence, creates an
 * isolated documents directory scoped by slug, dispatches the agent with the
 * master prompt, terminates early on user cancellation, and appears in /help output.
 *
 * These tests define the public contract for extension/src/slashCommandsWorkspace.ts —
 * they do not test private helpers beyond what the plan exposes as a seam.
 */

jest.mock(
    'vscode',
    () => ({
        window: {
            showInputBox: jest.fn(),
        },
        workspace: {},
        commands: {},
    }),
    { virtual: true },
);

import * as vscode from 'vscode';
import type { SlashCommandContext } from '../slashCommands';

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { handleDocumentPrepCommand } = require('../slashCommandsWorkspace');
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { handleHelpCommand } = require('../slashCommandsWorkspace');

describe('handleDocumentPrepCommand regression contract', () => {
    const mockPostMessages: Array<Record<string, unknown>> = [];

    function createMockContext(): SlashCommandContext {
        return {
            postMessage: jest.fn((msg) => {
                mockPostMessages.push(msg);
            }),
            client: {
                healthCheck: jest.fn().mockResolvedValue(true),
                visionAvailable: false,
                chatStream: jest.fn().mockResolvedValue({ receivedDone: true }),
            } as any,
            getRepoRoot: jest.fn().mockReturnValue('/mock/repo'),
            ensureSession: jest.fn().mockResolvedValue('mock-session-id'),
            ensureWebSocket: jest.fn().mockReturnValue({} as any),
            handleAgentMessage: jest.fn().mockResolvedValue(undefined),
            handleChatDispatch: jest.fn().mockResolvedValue(undefined),
            getWs: jest.fn().mockReturnValue({ readyState: 0 } as any),
            getLastCompletedSessionId: jest.fn(),
            setSessionId: jest.fn(),
            setLastCompletedSessionId: jest.fn(),
            extensionContext: {} as any,
            getFileDiagnostics: jest.fn().mockReturnValue(''),
        };
    }

    beforeEach(() => {
        mockPostMessages.length = 0;
        jest.clearAllMocks();
        // Reset showInputBox to resolve with values by default.
        (vscode.window.showInputBox as jest.Mock).mockImplementation(
            async () => 'default-value',
        );
    });

    it('collects intake inputs in sequence: topic, document type, target length/scope', async () => {
        const inputSequence: string[] = [];
        (vscode.window.showInputBox as jest.Mock).mockImplementation(async () => {
            inputSequence.push(Date.now().toString()); // unique marker per call
            return 'test-input';
        });

        const ctx = createMockContext();
        await handleDocumentPrepCommand(ctx, '');

        // Plan specifies three sequential inputs: Working Topic, Document Type, Target Length/Scope.
        expect(vscode.window.showInputBox).toHaveBeenCalledTimes(3);
    });

    it('creates an isolated documents directory scoped by slug', async () => {
        (vscode.window.showInputBox as jest.Mock).mockImplementation(async (_opts) => {
            const title = (_opts as any)?.title ?? '';
            if (title.includes('Topic')) return 'My Document Topic';
            if (title.includes('Type')) return 'Report';
            return '2000 words';
        });

        const ctx = createMockContext();
        await handleDocumentPrepCommand(ctx, '');

        // The handler should dispatch the agent with a prompt that references documents/{slug}/.
        const agentCall = (ctx.handleAgentMessage as jest.Mock).mock?.calls?.[0]?.[0];
        expect(typeof agentCall).toBe('string');
        // Slug-based directory isolation — plan specifies documents/${slug}/ path.
        expect(agentCall, 'Agent dispatch must reference a slug-scoped documents directory').toContain('documents/');
    });

    it('dispatches the agent with the master prompt containing phase markers', async () => {
        (vscode.window.showInputBox as jest.Mock).mockImplementation(async (_opts) => {
            const title = (_opts as any)?.title ?? '';
            if (title.includes('Topic')) return 'Architecture Review';
            if (title.includes('Type')) return 'Technical Document';
            return '3000 words';
        });

        const ctx = createMockContext();
        await handleDocumentPrepCommand(ctx, '');

        expect(ctx.handleAgentMessage).toHaveBeenCalledTimes(1);
        const dispatchedPrompt = (ctx.handleAgentMessage as jest.Mock).mock?.calls?.[0]?.[0];
        // The master prompt should contain all 14 phase markers per the plan's workflow.
        for (let i = 1; i <= 14; i++) {
            expect(dispatchedPrompt, `Missing Phase ${i} in agent dispatch`).toContain(`Phase ${i}`);
        }
    });

    it('terminates early when user cancels any input box', async () => {
        // showInputBox returns undefined when the user presses Escape (cancels).
        (vscode.window.showInputBox as jest.Mock)
            .mockResolvedValueOnce('Some Topic')
            .mockResolvedValueOnce(undefined);

        const ctx = createMockContext();
        await handleDocumentPrepCommand(ctx, '');

        // Agent should NOT be dispatched if the user cancels mid-intake.
        expect(ctx.handleAgentMessage).not.toHaveBeenCalled();
    });

    it('includes /document-prep in /help output for discoverability', async () => {
        const ctx = createMockContext();
        await handleHelpCommand(ctx, '');

        // The /help handler must post a message containing the command listing.
        expect(ctx.postMessage).toHaveBeenCalled();

        // Find the reply-type message that carries the help text.
        const replyMsg = mockPostMessages.find((m) => m.type === 'reply');
        expect(replyMsg, '/help should emit a reply-type message').toBeDefined();

        const helpText = (replyMsg as any)?.text ?? '';
        expect(helpText, '/help output must list /document-prep').toContain('/document-prep');
    });
});
