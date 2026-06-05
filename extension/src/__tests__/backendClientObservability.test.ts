jest.mock(
    "vscode",
    () => ({
        workspace: {
            getConfiguration: jest.fn(() => ({
                get: jest.fn(() => "http://localhost:8422"),
            })),
        },
    }),
    { virtual: true },
);

import { BackendClient } from "../backendClient";

describe("BackendClient observability routes", () => {
    const fetchMock = jest.fn();

    beforeEach(() => {
        fetchMock.mockReset();
        fetchMock.mockResolvedValue({
            ok: true,
            json: async () => ({}),
        });
        global.fetch = fetchMock as typeof fetch;
        (BackendClient as unknown as { instance?: BackendClient }).instance = undefined;
    });

    it("uses the FastAPI /api prefix for feedback submission", async () => {
        const client = BackendClient.getInstance();

        await client.submitFeedback("/repo", {
            session_id: "session-1",
            thumbs_up: true,
            rating: 5,
        });

        expect(fetchMock).toHaveBeenCalledWith(
            "http://localhost:8422/api/observability/feedback?"
                + "repo_root=%2Frepo&session_id=session-1&thumbs_up=true&rating=5",
            { method: "POST" },
        );
    });

    it("uses the FastAPI /api prefix for observability reads", async () => {
        const client = BackendClient.getInstance();

        await client.getObservabilitySessions("/repo");
        await client.getSessionDetail("/repo", "session-1");
        await client.getTraceTree("/repo", "session-1");
        await client.getFeedbackEntries("/repo", { session_id: "session-1" });
        await client.getMetricsSummary("/repo");

        const urls = fetchMock.mock.calls.map(([url]) => url);
        expect(urls).toEqual([
            "http://localhost:8422/api/observability/sessions?repo_root=%2Frepo",
            "http://localhost:8422/api/observability/sessions/session-1?repo_root=%2Frepo",
            "http://localhost:8422/api/observability/traces/tree?"
                + "repo_root=%2Frepo&session_id=session-1",
            "http://localhost:8422/api/observability/feedback?"
                + "repo_root=%2Frepo&session_id=session-1",
            "http://localhost:8422/api/observability/metrics/summary?repo_root=%2Frepo",
        ]);
    });
});
