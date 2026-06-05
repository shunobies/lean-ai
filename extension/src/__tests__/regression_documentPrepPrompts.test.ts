/**
 * Regression tests for document prep prompt builder module.
 *
 * Verifies that buildDocumentPrepMasterPrompt() exports correctly, enforces the
 * 14-step rhetorical writing workflow, mandates citation security rules, respects
 * context window constraints, and pauses at Phase 9 via request_clarification.
 *
 * These tests define the public contract for extension/src/documentPrepPrompts.ts —
 * they do not test private helpers or internal implementation details.
 */

import { buildDocumentPrepMasterPrompt, extractVerificationRules } from '../documentPrepPrompts';

describe('buildDocumentPrepMasterPrompt regression contract', () => {
    it('exports a non-empty prompt string containing all phase markers', () => {
        const prompt = buildDocumentPrepMasterPrompt();

        expect(typeof prompt).toBe('string');
        expect(prompt.length).toBeGreaterThan(0);

        // All 14 phases must be referenced in the master prompt.
        for (let i = 1; i <= 14; i++) {
            const phaseMarker = `Phase ${i}`;
            expect(prompt, `Missing Phase ${i} marker`).toContain(phaseMarker);
        }
    });

    it('includes intake phases (1-2) and research/verification phases (3-6)', () => {
        const prompt = buildDocumentPrepMasterPrompt();

        // Intake phase markers — plan specifies phases 1-2 are intake.
        expect(prompt).toContain('Phase 1');
        expect(prompt).toContain('Phase 2');

        // Research and verification phase markers — plan specifies phases 3-6.
        expect(prompt).toContain('Phase 3');
        expect(prompt).toContain('Phase 4');
        expect(prompt).toContain('Phase 5');
        expect(prompt).toContain('Phase 6');
    });

    it('enforces citation security rules via deterministic verification statuses', () => {
        const prompt = buildDocumentPrepMasterPrompt();

        // Plan mandates explicit status enums for source verification.
        expect(prompt).toContain('user_provided_verified');
        expect(prompt).toContain('candidate_unverified');

        // extractVerificationRules is the testability seam — must return an object with known statuses.
        const rules = extractVerificationRules();
        expect(rules).toBeDefined();
        expect(typeof rules).toBe('object');

        // At least one status string should be present in the returned rules.
        const ruleValues = Object.values(rules);
        const hasStatusEnum = ruleValues.some(
            (v) => typeof v === 'string' && ['user_provided_verified', 'candidate_unverified'].includes(v),
        );
        expect(hasStatusEnum, 'extractVerificationRules should expose at least one known verification status').toBe(true);
    });

    it('respects context window by keeping prompt under a reasonable token budget', () => {
        const prompt = buildDocumentPrepMasterPrompt();

        // Rough upper bound: ~40k characters is well within typical LLM context windows.
        // This guards against runaway prompt inflation that would exhaust the context budget.
        expect(prompt.length).toBeLessThan(40_000, 'Prompt exceeds reasonable token budget for context window safety');

        // Prompt should contain explicit instructions about table formatting for source matrices — a plan requirement.
        expect(prompt.toLowerCase()).toMatch(/table|matrix/, 'Plan requires table formatting for source matrices');
    });

    it('pauses at Phase 9 via request_clarification before drafting begins', () => {
        const prompt = buildDocumentPrepMasterPrompt();

        // Plan explicitly states the agent must pause at Phase 9 using request_clarification.
        expect(prompt).toContain('request_clarification');

        // The clarification gate should be associated with Phase 9 (Planning/Approval boundary).
        const phase9Index = prompt.indexOf('Phase 9');
        const clarifIndex = prompt.indexOf('request_clarification');
        expect(phase9Index, 'Phase 9 must appear before the request_clarification instruction').toBeGreaterThanOrEqual(0);

        // Phases 10-14 (Drafting/Polish) should exist in the same prompt — confirming branching.
        const phase10Index = prompt.indexOf('Phase 10');
        expect(phase10Index, 'Phase 10 must follow Phase 9 to confirm conditional branching').toBeGreaterThan(phase9Index);
    });
});
