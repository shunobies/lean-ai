/**
 * Test for handleHelpCommand documentation update
 * 
 * This test verifies that the /document-prep command is documented in the built-in help:
 * - The help output contains '/document-prep' string
 * - The description mentions '14-step' or 'rhetorical writing' for discoverability
 * 
 * @see extension/src/slashCommandsWorkspace.ts - The handleHelpCommand function
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

import { handleHelpCommand } from '../slashCommandsWorkspace';
import type { SlashCommandContext } from '../slashCommandsWorkspace';

describe('handleHelpCommand documentation includes /document-prep', () => {
    let mockPostMessageCalls: Array<{ text?: string }> = [];

    function createMockContext(): SlashCommandContext {
        return {
            client: {
                healthCheck: jest.fn().mockResolvedValue(true),
                visionAvailable: false,
                chatStream: jest.fn().mockResolvedValue({ receivedDone: true }),
            } as any,
            postMessage: (msg: unknown) => {
                mockPostMessageCalls.push(msg as { text?: string });
            },
            getRepoRoot: jest.fn().mockReturnValue('/mock/repo'),
            ensureSession: jest.fn().mockResolvedValue('mock-session-id'),
        } as SlashCommandContext;
    }

    beforeEach(() => {
        mockPostMessageCalls = [];
        jest.clearAllMocks();
    });

    it('help output contains /document-prep command', async () => {
        const ctx = createMockContext();

        await handleHelpCommand(ctx, '');

        // Find the reply message containing help text
        const replyCall = mockPostMessageCalls.find(
            (call) => call.text !== undefined && typeof call.text === 'string',
        );

        expect(replyCall).toBeDefined();
        expect(replyCall?.text).toContain('/document-prep');
    });

    it('help output mentions 14-step rhetorical writing workflow for /document-prep', async () => {
        const ctx = createMockContext();

        await handleHelpCommand(ctx, '');

        // Find the reply message containing help text
        const replyCall = mockPostMessageCalls.find(
            (call) => call.text !== undefined && typeof call.text === 'string',
        );

        expect(replyCall).toBeDefined();
        
        // Verify description mentions either '14-step' or 'rhetorical writing' for discoverability
        const helpText = replyCall?.text || '';
        const hasStepReference = /14[-\s]?step/i.test(helpText);
        const hasRhetoricalWriting = /rhetorical.*writing/i.test(helpText);
        
        expect(hasStepReference || hasRhetoricalWriting).toBe(true);
    });

    it('help output mentions source verification for /document-prep', async () => {
        const ctx = createMockContext();

        await handleHelpCommand(ctx, '');

        // Find the reply message containing help text
        const replyCall = mockPostMessageCalls.find(
            (call) => call.text !== undefined && typeof call.text === 'string',
        );

        expect(replyCall).toBeDefined();
        
        // Verify description mentions source verification or approval gates
        const helpText = replyCall?.text || '';
        const hasSourceVerification = /source.*verification/i.test(helpText);
        const hasApprovalGates = /approval.*gate/i.test(helpText);
        
        expect(hasSourceVerification || hasApprovalGates).toBe(true);
    });

    it('help output is sent via postMessage with reply type', async () => {
        const ctx = createMockContext();

        await handleHelpCommand(ctx, '');

        // Verify the message was posted
        expect(mockPostMessageCalls.length).toBeGreaterThan(0);
        
        const firstCall = mockPostMessageCalls[0];
        expect(firstCall).toEqual({
            type: 'reply',
            text: expect.any(String),
            cls: 'msg-system',
        });
    });
});
