jest.mock('vscode', () => ({ window: {} }), { virtual: true });

import { mockInterviewPrompt } from '../jobSearchPrompts';

describe('mockInterviewPrompt regression contract', () => {
    it('requires exact round numbering and duplicate prevention', () => {
        const prompt = mockInterviewPrompt({
            slug: 'acme_staff_engineer',
            difficulty: 'technical',
            questionCount: 5,
        });

        expect(prompt).toContain(
            'Question numbering format: every question MUST start with `### Question N of 5`',
        );
        expect(prompt).toContain(
            'Never repeat a question you have already asked, and never ask a semantically equivalent rephrase of an earlier question.',
        );
        expect(prompt).toContain(
            'keep an internal ledger of the question numbers and topics already used',
        );
        expect(prompt).toContain(
            'The number must increase by exactly one from the previous round',
        );
        expect(prompt).toContain(
            'Begin now: read the files, then ask `### Question 1 of 5`.',
        );
    });
});
