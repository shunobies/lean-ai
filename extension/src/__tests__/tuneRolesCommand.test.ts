jest.mock(
    'vscode',
    () => ({
        window: {
            showInformationMessage: jest.fn(),
        },
        workspace: {},
        commands: {},
    }),
    { virtual: true },
);

import * as vscode from 'vscode';
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
                            selected_role_title: 'Codebase Maintainer',
                            runtime_reliability_score: 92,
                            issues_found: [],
                            suggestions_available: false,
                            affected_prompt_keys: [],
                            runtime_evaluation_status: 'current',
                            warning: null,
                        },
                        {
                            role: 'request',
                            model_id: 'ollama:chat',
                            status: 'skipped',
                            profile_path: '/repo/.lean_ai/role_tuning/request--ollama-chat.json',
                            prompts_path: '/repo/.lean_ai/prompts.yaml',
                            selected_role_title: 'Product Owner',
                            runtime_reliability_score: 71,
                            issues_found: ['Drifts into technical design'],
                            suggestions_available: true,
                            affected_prompt_keys: ['fix.request_system'],
                            runtime_evaluation_status: 'current',
                            warning: 'Role tuning was judged by the same model being calibrated.',
                        },
                    ],
                }),
                applyRoleTuningSuggestions: jest.fn().mockResolvedValue({
                    role: 'request',
                    model_id: 'ollama:chat',
                    status: 'applied',
                    profile_path: '/repo/.lean_ai/role_tuning/request--ollama-chat.json',
                    prompts_path: '/repo/.lean_ai/prompts.yaml',
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
        (vscode.window.showInformationMessage as jest.Mock).mockResolvedValue('Skip');

        await handleTuneRolesCommand(ctx, '');

        expect(ctx.client.prewarmRoleTuning).toHaveBeenCalledWith('/repo', undefined);
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
                text: expect.stringContaining('Suggestions available: 1'),
            }),
        );
        expect(ctx.postMessage).toHaveBeenCalledWith(
            expect.objectContaining({
                type: 'reply',
                text: expect.stringContaining('Warnings:'),
            }),
        );
    });

    it('supports targeting a single role and applying approved suggestions', async () => {
        const ctx = createContext();
        (vscode.window.showInformationMessage as jest.Mock).mockResolvedValue('Apply');

        await handleTuneRolesCommand(ctx, 'request');

        expect(ctx.client.prewarmRoleTuning).toHaveBeenCalledWith('/repo', 'request');
        expect(ctx.client.applyRoleTuningSuggestions).toHaveBeenCalledWith('/repo', 'request');
    });

    it('rejects invalid role arguments', async () => {
        const ctx = createContext();

        await handleTuneRolesCommand(ctx, 'nope');

        expect(ctx.postMessage).toHaveBeenCalledWith(
            expect.objectContaining({
                type: 'error',
                text: expect.stringContaining('Usage: `/tune-roles [primary|request|expert]`'),
            }),
        );
        expect(ctx.client.prewarmRoleTuning).not.toHaveBeenCalled();
    });
});
