import { handleWsMessage } from '../wsHandler';

describe('handleWsMessage clarification payloads', () => {
    function createContext() {
        return {
            postMessage: jest.fn(),
            closeWebSocket: jest.fn(),
            clearSession: jest.fn(),
        };
    }

    it('normalizes a single clarification question string into one bullet', () => {
        const ctx = createContext();

        handleWsMessage(
            {
                type: 'clarification_needed',
                questions: 'Should we support SQLite or only PostgreSQL?',
            } as any,
            ctx,
        );

        const replyCall = ctx.postMessage.mock.calls.find(
            ([msg]) => msg?.type === 'reply' && typeof msg?.text === 'string' && String(msg.text).includes('Clarification needed'),
        );
        expect(replyCall).toBeDefined();
        expect(replyCall?.[0]?.text).toContain('- Should we support SQLite or only PostgreSQL?');
        expect(replyCall?.[0]?.text).not.toContain('- S\n- h\n- o\n- u');
    });

    it('preserves multi-question clarification arrays', () => {
        const ctx = createContext();

        handleWsMessage(
            {
                type: 'clarification_needed',
                questions: ['Question one?', 'Question two?'],
            } as any,
            ctx,
        );

        const replyCall = ctx.postMessage.mock.calls.find(
            ([msg]) => msg?.type === 'reply' && typeof msg?.text === 'string' && String(msg.text).includes('Clarification needed'),
        );
        expect(replyCall?.[0]?.text).toContain('- Question one?');
        expect(replyCall?.[0]?.text).toContain('- Question two?');
    });
});
