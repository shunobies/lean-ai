import { handleWsMessage } from '../wsHandler';

function createContext(sessionId = 'sess-123') {
    return {
        postMessage: jest.fn(),
        closeWebSocket: jest.fn(),
        clearSession: jest.fn(),
        getActiveSessionId: jest.fn(() => sessionId),
    };
}

describe('handleWsMessage execution checkpoint + feedback wiring', () => {
    it('maps execution_checkpoint messages into checklist updates', () => {
        const ctx = createContext();

        handleWsMessage(
            {
                type: 'execution_checkpoint',
                step: 2,
                total: 5,
                tool: 'edit_file',
                description: 'Step 3: update handler',
                status: 'running',
                file_path: 'src/handler.ts',
            } as any,
            ctx,
        );

        expect(ctx.postMessage).toHaveBeenCalledWith({
            type: 'checkpointUpdate',
            stepIndex: 2,
            description: 'Step 3: update handler',
            status: 'running',
        });
    });

    it('attaches feedback targets to assistant workflow bubbles', () => {
        const ctx = createContext('sess-feedback');

        handleWsMessage(
            {
                type: 'assistant_content',
                content: 'Done with the change.',
                done: true,
            } as any,
            ctx,
        );

        expect(ctx.postMessage).toHaveBeenCalledWith({
            type: 'reply',
            text: 'Done with the change.',
            cls: 'msg-ai',
            streaming: false,
            done: true,
            feedbackTarget: { session_id: 'sess-feedback' },
        });
    });

    it('attaches feedback targets to tool progress bubbles', () => {
        const ctx = createContext('sess-tools');

        handleWsMessage(
            {
                type: 'tool_progress',
                description: 'Running tests',
                status: 'completed',
            } as any,
            ctx,
        );

        expect(ctx.postMessage).toHaveBeenCalledWith({
            type: 'reply',
            text: 'Running tests ✓',
            cls: 'msg-system',
            feedbackTarget: { session_id: 'sess-tools' },
        });
    });
});
