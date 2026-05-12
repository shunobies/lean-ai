export const NO_FOLLOW_UP_ASSISTANT_NOTE =
    "Tools completed; no follow-up answer was returned.";

export type AssistantStreamChannel = "chat" | "workflow";

export interface AssistantStreamSegmentationState {
    activeChannel: AssistantStreamChannel | null;
    awaitingAssistantAfterInterrupt: boolean;
}

export type AssistantStreamSegmentationEvent =
    | { type: "assistant_chunk"; channel: AssistantStreamChannel }
    | { type: "interrupt" }
    | { type: "finalize"; channel?: AssistantStreamChannel | null }
    | {
        type: "turn_done";
        channel?: AssistantStreamChannel | null;
        showNoFollowUpNote?: boolean;
    }
    | { type: "reset" };

export interface AssistantStreamSegmentationActions {
    startSegment: boolean;
    finalizeSegment: boolean;
    markInterrupted: boolean;
    showNoFollowUpNote: boolean;
}

export function createInitialAssistantStreamSegmentationState(): AssistantStreamSegmentationState {
    return {
        activeChannel: null,
        awaitingAssistantAfterInterrupt: false,
    };
}

export function reduceAssistantStreamSegmentationState(
    state: AssistantStreamSegmentationState,
    event: AssistantStreamSegmentationEvent,
): {
    state: AssistantStreamSegmentationState;
    actions: AssistantStreamSegmentationActions;
} {
    if (event.type === "reset") {
        return {
            state: createInitialAssistantStreamSegmentationState(),
            actions: {
                startSegment: false,
                finalizeSegment: false,
                markInterrupted: false,
                showNoFollowUpNote: false,
            },
        };
    }

    const next: AssistantStreamSegmentationState = { ...state };
    const actions: AssistantStreamSegmentationActions = {
        startSegment: false,
        finalizeSegment: false,
        markInterrupted: false,
        showNoFollowUpNote: false,
    };

    switch (event.type) {
        case "assistant_chunk":
            if (next.activeChannel && next.activeChannel !== event.channel) {
                actions.finalizeSegment = true;
                next.activeChannel = null;
            }
            if (!next.activeChannel) {
                actions.startSegment = true;
                next.activeChannel = event.channel;
            }
            next.awaitingAssistantAfterInterrupt = false;
            break;

        case "interrupt":
            if (next.activeChannel) {
                actions.finalizeSegment = true;
                actions.markInterrupted = true;
                next.activeChannel = null;
                next.awaitingAssistantAfterInterrupt = true;
            }
            break;

        case "finalize":
            if (next.activeChannel && (!event.channel || next.activeChannel === event.channel)) {
                actions.finalizeSegment = true;
                next.activeChannel = null;
            }
            break;

        case "turn_done":
            if (next.activeChannel && (!event.channel || next.activeChannel === event.channel)) {
                actions.finalizeSegment = true;
                next.activeChannel = null;
            }
            if (event.showNoFollowUpNote && next.awaitingAssistantAfterInterrupt) {
                actions.showNoFollowUpNote = true;
            }
            next.awaitingAssistantAfterInterrupt = false;
            break;
    }

    return { state: next, actions };
}
