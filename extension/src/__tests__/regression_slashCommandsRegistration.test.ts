/**
 * Regression tests for slash command routing map registration.
 *
 * Verifies that createSlashCommands returns a properly constructed Map with
 * all expected route keys, binds handlers correctly to their routes, and
 * delegates dispatch calls through the map to the underlying handler functions.
 *
 * These tests define the public contract for extension/src/slashCommands.ts —
 * they do not test private helpers or internal implementation details.
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

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { createSlashCommands } = require('../slashCommands');

describe('createSlashCommands routing map regression contract', () => {
    function createMockContext() {
        return {
            postMessage: jest.fn(),
            client: {
                healthCheck: jest.fn().mockResolvedValue(true),
            } as any,
            getRepoRoot: jest.fn().mockReturnValue('/mock/repo'),
            ensureSession: jest.fn().mockResolvedValue('mock-session-id'),
            ensureWebSocket: jest.fn().mockReturnValue({} as any),
            handleAgentMessage: jest.fn().mockResolvedValue(undefined),
            handleChatDispatch: jest.fn().mockResolvedValue(undefined),
            getWs: jest.fn(),
            getLastCompletedSessionId: jest.fn(),
            setSessionId: jest.fn(),
            setLastCompletedSessionId: jest.fn(),
            extensionContext: { globalState: {} } as any,
            getFileDiagnostics: jest.fn().mockReturnValue(''),
        };
    }

    it('returns a Map containing all expected route keys including /document-prep', () => {
        const ctx = createMockContext();
        const map = createSlashCommands(ctx);

        expect(map).toBeInstanceOf(Map);

        // Core routes that must exist per the existing registration pattern.
        const requiredRoutes = [
            '/init',
            '/scaffold',
            '/agent',
            '/fix',
            '/request',
            '/style',
            '/reboot',
            '/approve',
            '/reject',
            '/resume',
            '/note',
            '/interview-prep',
            '/help',
            '/document-prep',
        ];

        for (const route of requiredRoutes) {
            expect(map.has(route), `Routing map must contain key ${route}`).toBe(true);
        }
    });

    it('binds /document-prep to handleDocumentPrepCommand handler function', () => {
        const ctx = createMockContext();
        const map = createSlashCommands(ctx);

        // The route must be bound to a callable function.
        expect(map.has('/document-prep')).toBe(true);
        const handler = map.get('/document-prep');
        expect(typeof handler, '/document-prep must resolve to a function').toBe('function');

        // Existing routes should also still be bound (no clobbering).
        const interviewHandler = map.get('/interview-prep');
        expect(typeof interviewHandler, '/interview-prep must still be a function after /document-prep registration').toBe('function');
    });

    it('delegates dispatch through the Map to invoke handler with context and args', async () => {
        const ctx = createMockContext();
        const map = createSlashCommands(ctx);

        // Invoke the /help route as a delegation test — it is simple and safe.
        const helpHandler = map.get('/help');
        expect(helpHandler).toBeDefined();

        await helpHandler('');

        // The handler should have posted at least one message via ctx.postMessage.
        expect(ctx.postMessage).toHaveBeenCalled();
    });
});
