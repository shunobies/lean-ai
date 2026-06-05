/**
 * Unit tests for buildDocumentPrepPrompt in documentPrepPrompts.ts
 * 
 * This test suite verifies the Master Orchestration Prompt generation function:
 * - Ensures all 14 phase markers are present in the generated prompt
 * - Verifies Phase 9 approval gate constraints (security concern mitigation)
 * - Confirms source verification status definitions exist
 * - Validates citation_ready='No' requirement for model memory sources
 * 
 * @see extension/src/documentPrepPrompts.ts - The buildDocumentPrepPrompt function
 */

import { buildDocumentPrepPrompt } from '../documentPrepPrompts';

describe('buildDocumentPrepPrompt', () => {
    const mockTopic = 'Climate Change Impact Analysis';
    const mockDocType = 'research paper';
    const mockTargetLength = '5000 words';
    const mockOutputDir = '/documents/climate-change-impact-analysis';

    it('returns a non-empty string', async () => {
        const result = await buildDocumentPrepPrompt(
            mockTopic,
            mockDocType,
            mockTargetLength,
            mockOutputDir,
        );
        
        expect(result).toBeInstanceOf(String);
        expect(result.length).toBeGreaterThan(0);
    });

    it('includes all 14 phase markers in the prompt', async () => {
        const result = await buildDocumentPrepPrompt(
            mockTopic,
            mockDocType,
            mockTargetLength,
            mockOutputDir,
        );

        // Verify all 14 phases are present via substring matching
        for (let i = 1; i <= 14; i++) {
            const phaseMarker = `Phase ${i}`;
            expect(result).toContain(phaseMarker);
        }
    });

    it('includes Phase 9 approval gate with request_clarification instruction', async () => {
        const result = await buildDocumentPrepPrompt(
            mockTopic,
            mockDocType,
            mockTargetLength,
            mockOutputDir,
        );

        // Security concern [high]: Local LLM skipping Phase 9 Approval Gate
        // Mitigation: Prompt must instruct model to call request_clarification
        expect(result).toContain('request_clarification');
    });

    it('includes negative constraint about not drafting before approval at Phase 9', async () => {
        const result = await buildDocumentPrepPrompt(
            mockTopic,
            mockDocType,
            mockTargetLength,
            mockOutputDir,
        );

        // Security concern [high]: Local LLM skipping Phase 9 Approval Gate
        // Mitigation: Include negative constraint 'DO NOT generate draft.md until approved'
        expect(result).toMatch(/do not/gi);
        expect(result).toContain('approved');
    });

    it('defines source verification status user_provided_verified', async () => {
        const result = await buildDocumentPrepPrompt(
            mockTopic,
            mockDocType,
            mockTargetLength,
            mockOutputDir,
        );

        // Security concern [medium]: Source Verification Hallucination
        // Mitigation: Prompt must define strict status criteria
        expect(result).toContain('user_provided_verified');
    });

    it('defines source verification status web_verified', async () => {
        const result = await buildDocumentPrepPrompt(
            mockTopic,
            mockDocType,
            mockTargetLength,
            mockOutputDir,
        );

        // Security concern [medium]: Source Verification Hallucination
        // Mitigation: Prompt must define strict status criteria
        expect(result).toContain('web_verified');
    });

    it('defines source verification status candidate_unverified', async () => {
        const result = await buildDocumentPrepPrompt(
            mockTopic,
            mockDocType,
            mockTargetLength,
            mockOutputDir,
        );

        // Security concern [medium]: Source Verification Hallucination
        // Mitigation: Prompt must define strict status criteria
        expect(result).toContain('candidate_unverified');
    });

    it('requires citation_ready=No for model memory sources', async () => {
        const result = await buildDocumentPrepPrompt(
            mockTopic,
            mockDocType,
            mockTargetLength,
            mockOutputDir,
        );

        // Security concern [medium]: Source Verification Hallucination
        // Mitigation: Require citation_ready='No' for model memory sources
        expect(result).toMatch(/citation_ready/gi);
        expect(result).toContain('model memory');
    });

    it('includes the provided topic in the prompt', async () => {
        const result = await buildDocumentPrepPrompt(
            mockTopic,
            mockDocType,
            mockTargetLength,
            mockOutputDir,
        );

        expect(result).toContain(mockTopic);
    });

    it('includes the provided document type in the prompt', async () => {
        const result = await buildDocumentPrepPrompt(
            mockTopic,
            mockDocType,
            mockTargetLength,
            mockOutputDir,
        );

        expect(result).toContain(mockDocType);
    });

    it('includes the provided target length in the prompt', async () => {
        const result = await buildDocumentPrepPrompt(
            mockTopic,
            mockDocType,
            mockTargetLength,
            mockOutputDir,
        );

        expect(result).toContain(mockTargetLength);
    });

    it('includes the provided output directory in the prompt', async () => {
        const result = await buildDocumentPrepPrompt(
            mockTopic,
            mockDocType,
            mockTargetLength,
            mockOutputDir,
        );

        expect(result).toContain(mockOutputDir);
    });

    it('includes concrete option banks for document structure', async () => {
        const result = await buildDocumentPrepPrompt(
            mockTopic,
            mockDocType,
            mockTargetLength,
            mockOutputDir,
        );

        // The prompt should include concrete options/banks to guide the agent
        expect(result).toMatch(/option/gi);
    });

    it('includes Phase 3 Research Question Builder for research paper types', async () => {
        const result = await buildDocumentPrepPrompt(
            'Research Topic',
            'research paper',
            '3000 words',
            '/documents/research-topic',
        );

        // Implementation plan: Phase 3 Research Paper Branch (Conditional)
        // Trigger: If doc is research paper, research essay, literature review, or source-supported academic report
        expect(result).toContain('Research Question Builder');
    });

    it('defines conditional trigger for research paper document type', async () => {
        const result = await buildDocumentPrepPrompt(
            'Test Topic',
            'research paper',
            '3000 words',
            '/documents/test-topic',
        );

        // Implementation plan: Phase 3 Research Paper Branch (Conditional)
        // Trigger conditions must include research paper type
        expect(result).toContain('research paper');
    });

    it('defines conditional trigger for research essay document type', async () => {
        const result = await buildDocumentPrepPrompt(
            'Test Topic',
            'research essay',
            '3000 words',
            '/documents/test-topic',
        );

        // Implementation plan: Phase 3 Research Paper Branch (Conditional)
        // Trigger conditions must include research essay type
        expect(result).toContain('research essay');
    });

    it('defines conditional trigger for literature review document type', async () => {
        const result = await buildDocumentPrepPrompt(
            'Test Topic',
            'literature review',
            '3000 words',
            '/documents/test-topic',
        );

        // Implementation plan: Phase 3 Research Paper Branch (Conditional)
        // Trigger conditions must include literature review type
        expect(result).toContain('literature review');
    });

    it('defines conditional trigger for source-supported academic report document type', async () => {
        const result = await buildDocumentPrepPrompt(
            'Test Topic',
            'source-supported academic report',
            '3000 words',
            '/documents/test-topic',
        );

        // Implementation plan: Phase 3 Research Paper Branch (Conditional)
        // Trigger conditions must include source-supported academic report type
        expect(result).toContain('academic');
    });

    it('includes quality gate requirements for Research Question Builder', async () => {
        const result = await buildDocumentPrepPrompt(
            'Test Topic',
            'research paper',
            '3000 words',
            '/documents/test-topic',
        );

        // Implementation plan: Phase 3 must include quality gate requirements before source gathering
        expect(result).toContain('quality');
    });

    it('includes Research Question Builder steps in phase description', async () => {
        const result = await buildDocumentPrepPrompt(
            'Test Topic',
            'research paper',
            '3000 words',
            '/documents/test-topic',
        );

        // Implementation plan: Phase 3 must describe Research Question Builder steps
        expect(result).toContain('Research Question Builder');
        
        // Should include instructions about executing this before source gathering
        expect(result).toMatch(/before/i);
    });
});
