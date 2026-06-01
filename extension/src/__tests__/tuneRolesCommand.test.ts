jest.mock(
    'vscode',
    () => ({
        window: {},
        workspace: {},
        commands: {},
    }),
    { virtual: true },
);

import { handleTuneRolesCommand } from '../slashCommandsWorkspace';
import type { SlashCommandContext } from '../slashCommands';

describe('handleTuneRolesCommand', () => {
    function createContext(): SlashCommandContext {
        return {
            postMessage: jest.fn(),
            client: {
                healthCheck: jest.fn().mockResolvedValue(true),
                prewarmRoleTuning: jest.fn().mockResolvedValue({
                    results: [
                        {
                            role: 'primary',
                            model_id: 'ollama:coder',
                            status: 'tuned',
                            profile_path: '/repo/.lean_ai/role_tuning/primary--ollama-coder.json',
                            prompts_path: '/repo/.lean_ai/prompts.yaml',
                            warning: null,
                        },
                        {
                            role: 'request',
                            model_id: 'ollama:chat',
                            status: 'skipped',
                            profile_path: '/repo/.lean_ai/role_tuning/request--ollama-chat.json',
                            prompts_path: '/repo/.lean_ai/prompts.yaml',
                            warning: 'Role tuning was judged by the same model being calibrated.',
                        },
                    ],
                }),
            } as any,
            getRepoRoot: jest.fn().mockReturnValue('/repo'),
            ensureSession: jest.fn(),
            ensureWebSocket: jest.fn(),
            handleAgentMessage: jest.fn(),
            handleChatDispatch: jest.fn(),
            getWs: jest.fn(),
            getLastCompletedSessionId: jest.fn(),
            setSessionId: jest.fn(),
            setLastCompletedSessionId: jest.fn(),
            extensionContext: { globalState: { update: jest.fn() } } as any,
            getFileDiagnostics: jest.fn(),
        };
    }

    it('renders a concise tuned vs skipped summary', async () => {
        const ctx = createContext();

        await handleTuneRolesCommand(ctx, '');

        expect(ctx.client.prewarmRoleTuning).toHaveBeenCalledWith('/repo');
        expect(ctx.postMessage).toHaveBeenCalledWith(
            expect.objectContaining({
                type: 'reply',
                cls: 'msg-system',
                text: expect.stringContaining('Tuned: 1'),
            }),
        );
        expect(ctx.postMessage).toHaveBeenCalledWith(
            expect.objectContaining({
                type: 'reply',
                text: expect.stringContaining('Skipped (already current): 1'),
            }),
        );
        expect(ctx.postMessage).toHaveBeenCalledWith(
            expect.objectContaining({
                type: 'reply',
                text: expect.stringContaining('Warnings:'),
            }),
        );
    });
});
