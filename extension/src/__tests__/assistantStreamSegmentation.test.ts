import {
    createInitialAssistantStreamSegmentationState,
    reduceAssistantStreamSegmentationState,
} from "../assistantStreamSegmentation";

describe("assistant stream segmentation", () => {
    it("splits assistant text into draft and final segments around tool activity", () => {
        let state = createInitialAssistantStreamSegmentationState();

        let result = reduceAssistantStreamSegmentationState(state, {
            type: "assistant_chunk",
            channel: "chat",
        });
        expect(result.actions.startSegment).toBe(true);
        state = result.state;

        result = reduceAssistantStreamSegmentationState(state, { type: "interrupt" });
        expect(result.actions.finalizeSegment).toBe(true);
        expect(result.actions.markInterrupted).toBe(true);
        state = result.state;

        result = reduceAssistantStreamSegmentationState(state, {
            type: "assistant_chunk",
            channel: "chat",
        });
        expect(result.actions.startSegment).toBe(true);
        expect(result.actions.finalizeSegment).toBe(false);
    });

    it("keeps multiple tool interruptions collapsed into one awaited follow-up", () => {
        let state = createInitialAssistantStreamSegmentationState();

        state = reduceAssistantStreamSegmentationState(state, {
            type: "assistant_chunk",
            channel: "chat",
        }).state;

        let result = reduceAssistantStreamSegmentationState(state, { type: "interrupt" });
        expect(result.actions.finalizeSegment).toBe(true);
        state = result.state;

        result = reduceAssistantStreamSegmentationState(state, { type: "interrupt" });
        expect(result.actions.finalizeSegment).toBe(false);
        expect(result.state.awaitingAssistantAfterInterrupt).toBe(true);

        result = reduceAssistantStreamSegmentationState(result.state, {
            type: "assistant_chunk",
            channel: "chat",
        });
        expect(result.actions.startSegment).toBe(true);
        expect(result.state.awaitingAssistantAfterInterrupt).toBe(false);
    });

    it("allows tool activity before any assistant text without forcing a draft segment", () => {
        const result = reduceAssistantStreamSegmentationState(
            createInitialAssistantStreamSegmentationState(),
            { type: "interrupt" },
        );

        expect(result.actions.finalizeSegment).toBe(false);
        expect(result.actions.markInterrupted).toBe(false);
        expect(result.state.awaitingAssistantAfterInterrupt).toBe(false);
    });

    it("shows a no-follow-up note when tools finish and the assistant never resumes", () => {
        let state = createInitialAssistantStreamSegmentationState();

        state = reduceAssistantStreamSegmentationState(state, {
            type: "assistant_chunk",
            channel: "chat",
        }).state;
        state = reduceAssistantStreamSegmentationState(state, { type: "interrupt" }).state;

        const result = reduceAssistantStreamSegmentationState(state, {
            type: "turn_done",
            channel: "chat",
            showNoFollowUpNote: true,
        });

        expect(result.actions.showNoFollowUpNote).toBe(true);
        expect(result.state.awaitingAssistantAfterInterrupt).toBe(false);
    });

    it("splits workflow assistant content around tool progress and resumes cleanly", () => {
        let state = createInitialAssistantStreamSegmentationState();

        state = reduceAssistantStreamSegmentationState(state, {
            type: "assistant_chunk",
            channel: "workflow",
        }).state;

        let result = reduceAssistantStreamSegmentationState(state, { type: "interrupt" });
        expect(result.actions.finalizeSegment).toBe(true);
        expect(result.actions.markInterrupted).toBe(true);
        state = result.state;

        result = reduceAssistantStreamSegmentationState(state, {
            type: "assistant_chunk",
            channel: "workflow",
        });
        expect(result.actions.startSegment).toBe(true);
    });

    it("resets stream state cleanly between turns", () => {
        let state = createInitialAssistantStreamSegmentationState();

        state = reduceAssistantStreamSegmentationState(state, {
            type: "assistant_chunk",
            channel: "workflow",
        }).state;
        state = reduceAssistantStreamSegmentationState(state, { type: "interrupt" }).state;

        const result = reduceAssistantStreamSegmentationState(state, { type: "reset" });
        expect(result.state).toEqual(createInitialAssistantStreamSegmentationState());
        expect(result.actions.finalizeSegment).toBe(false);
    });
});
