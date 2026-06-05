/**
 * Master orchestration prompt builder for the document-prep command family.
 *
 * Constructs a 14-phase workflow prompt that guides the agent through intake,
 * research/verification, planning/approval, and drafting/polish stages when
 * producing long-form documents. Keeps prompts in a separate module so they
 * are diff-friendly and slash-command handlers stay focused on VS Code UI plumbing.
 */

// ---------------------------------------------------------------------------
// Verification status enums — used by the prompt and exposed as a test seam
// ---------------------------------------------------------------------------

/** Deterministic verification statuses for source material. */
export const VerificationStatus = {
    /** User explicitly provided and confirmed this source. */
    USER_PROVIDED_VERIFIED: "user_provided_verified",
    /** Agent found a candidate source but has not yet verified it. */
    CANDIDATE_UNVERIFIED: "candidate_unverified",
    /** Agent fetched and validated the source content. */
    VERIFIED_READY: "verified_ready",
    /** Source could not be reached or was unreliable — must not be cited. */
    FAILED_INVALID: "failed_invalid",
} as const;

/** Type alias for verification status strings. */
export type VerificationStatusValue = (typeof VerificationStatus)[keyof typeof VerificationStatus];

// ---------------------------------------------------------------------------
// Testability seam — pure helper extracted so tests can assert rule content
// without parsing the full prompt string
// ---------------------------------------------------------------------------

/**
 * Return an object describing the verification rules enforced by the master
 * prompt.  This is a pure function (no side-effects) and exists as a test
 * seam so regression tests can inspect status enums and citation logic
 * independently of the concatenated prompt builder.
 */
export function extractVerificationRules(): Record<string, string> {
    return {
        userProvidedVerified: VerificationStatus.USER_PROVIDED_VERIFIED,
        candidateUnverified: VerificationStatus.CANDIDATE_UNVERIFIED,
        verifiedReady: VerificationStatus.VERIFIED_READY,
        failedInvalid: VerificationStatus.FAILED_INVALID,
        citationLogic:
            "Every factual claim must carry an inline citation referencing a source with its verification status. " +
            'Sources marked as candidate_unverified MUST be verified (fetched and validated) before citing — ' +
            "if verification fails, mark them failed_invalid and do NOT use them in the final draft. " +
            "Only sources at user_provided_verified or verified_ready may appear in citation tables.",
    };
}

// ---------------------------------------------------------------------------
// Master prompt builder
// ---------------------------------------------------------------------------

/**
 * Build the master orchestration prompt for document preparation.
 *
 * The returned string enforces a strict 14-phase workflow covering intake,
 * research, planning, drafting and polish. It mandates table formatting
 * for source matrices, defines deterministic verification statuses, and
 * pauses at Phase 9 to request clarification from the user before any
 * drafting begins.
 */
export function buildDocumentPrepMasterPrompt(
    topic = "document-prep",
    documentType = "report",
    targetLength = "standard length",
): string {
    const statusList = [
        VerificationStatus.USER_PROVIDED_VERIFIED,
        VerificationStatus.CANDIDATE_UNVERIFIED,
        VerificationStatus.VERIFIED_READY,
        VerificationStatus.FAILED_INVALID,
    ];

    return [
        // ---- Role & scope ---------------------------------------------------
        "You are a research and writing assistant. Your job is to produce a high-quality long-form document by following the 14-step workflow below EXACTLY in order.",
        "",
        `Topic: ${topic}`,
        `Document type: ${documentType}`,
        `Target length / scope: ${targetLength}`,
        "",

        // ---- Phase 1-2: Intake ----------------------------------------------
        "=== Phase 1 — Requirements Capture ===",
        "Restate the topic, document type and target length in your own words so I can confirm you understood the brief. Do not start researching yet.",
        "",
        "=== Phase 2 — Scope Confirmation ===",
        "List any ambiguities or missing details that could affect the final output. Keep this list to three items or fewer, then proceed only after confirming scope is clear.",
        "",

        // ---- Phase 3-6: Research & Verification -----------------------------
        "=== Phase 3 — Source Discovery ===",
        "Search for relevant sources using search_internet and fetch_url. Prefer authoritative, recent material over opinion pieces. Record every candidate in a source matrix table (see formatting rules below).",
        "",
        "=== Phase 4 — Source Verification ===",
        'For each candidate source, set its verification status to one of: ' + statusList.join(", ") + '.',
        "- Fetch the URL and validate that the content actually supports what you plan to cite.",
        '- Mark sources as verified_ready when confirmed. Mark them failed_invalid if unreachable or unreliable.',
        "",
        "=== Phase 5 — Evidence Extraction ===",
        "Pull specific facts, data points and quotes from verified sources. Tag each piece of evidence with the source URL and its verification status. Do NOT invent statistics or attribute claims to unverified sources.",
        "",
        "=== Phase 6 — Knowledge Synthesis ===",
        "Combine extracted evidence into a structured knowledge outline that maps directly to planned document sections. Flag any gaps where verified evidence is insufficient.",
        "",

        // ---- Phase 7-9: Planning & Approval ----------------------------------
        "=== Phase 7 — Outline Construction ===",
        "Produce a detailed section-by-section outline with estimated word counts per section so the total matches the target length / scope. Include sub-headings and one-sentence summaries.",
        "",
        "=== Phase 8 — Gap Analysis ===",
        "Review the outline against available verified evidence. Identify sections where additional research is needed or where you will rely on general knowledge (clearly labelled as analysis, not cited fact).",
        "",
        "=== Phase 9 — Approval Gate ===",
        "STOP and present the final outline for review. Use request_clarification to pause and ask me whether to proceed with drafting or if changes are needed. Do NOT begin writing the document body until I explicitly approve.",
        "",

        // ---- Phase 10-14: Drafting & Polish ----------------------------------
        "=== Phase 10 — First Draft ===",
        "Write the full draft following the approved outline. Use evidence from verified sources. Inline-cite every factual claim with a bracketed reference to the source table row number.",
        "",
        "=== Phase 11 — Citation Audit ===",
        'Cross-check every inline citation against the source matrix. Remove or replace any citations pointing to failed_invalid sources. Ensure all claims have backing evidence.',
        "",
        "=== Phase 12 — Tone and Style Pass ===",
        "Review for clarity, coherence and professional tone. Fix awkward transitions between sections. Make sure headings form a logical hierarchy.",
        "",
        "=== Phase 13 — Final Polish ===",
        "Proofread for grammar, spelling and formatting consistency. Trim any remaining AI-isms: avoid phrases like 'delve into', 'it is important to note', 'in today's world', 'leveraging', 'tapestry of', 'testament to'. Write in a direct, active voice.",
        "",
        "=== Phase 14 — Delivery ===",
        "Save the finished document to documents/{slug}/ with an appropriate filename. Append a source verification table at the end listing every cited URL, its title/topic and final verification status.",
        "",

        // ---- Formatting rules ------------------------------------------------
        "=== Source Matrix Format Rules ===",
        "All source matrices MUST use Markdown tables with these columns:",
        "| # | URL | Title / Topic | Verification Status | Notes |",
        "- Keep URLs short (use the canonical landing page, not tracking links).",
        '- Verification status must be one of: ' + statusList.join(", ") + '.',
        "",

        // ---- Negative constraints --------------------------------------------
        "=== Hard Constraints ===",
        "- DO NOT skip phases. Follow them in order.",
        "- DO NOT fabricate sources, statistics or quotes.",
        '- DO NOT cite a source unless its verification status is user_provided_verified or verified_ready.',
        "- DO NOT use filler language or generic AI-isms (see Phase 13).",
        "- DO NOT proceed past Phase 9 without explicit approval via request_clarification.",
        "- If research reveals the topic cannot be covered with reliable sources, say so at Phase 6 and adjust scope before asking for approval at Phase 9.",

    ].join("\n");
}
